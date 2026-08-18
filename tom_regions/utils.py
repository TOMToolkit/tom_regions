"""Geometry utilities for tom_regions.

This module is the seam between mocpy (a pure-MOC library that doesn't
know about Django) and the Django ORM (which doesn't know about MOCs).
The functions here translate MOCs into RegionTile rows, recompute cached
geometric summaries on a Region, and offer simple helper queries that
don't deserve a custom Manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from psycopg2.extras import NumericRange

from tom_regions.healpix_django.constants import PIXEL_AREA_STER
from tom_regions.healpix_django.encoding import (
    moc_to_ranges,
    range_to_level_ipix,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from mocpy import MOC
    from tom_regions.models import Region


def materialize_tiles(
    region: "Region",
    tiles: "Iterable[tuple[int, int, float | None]]",
    *,
    batch_size: int = 1000,
) -> int:
    """Insert ``RegionTile`` rows from ``(lower, upper, probdensity)`` triples.

    This is the single low-level path that turns deepest-level ``int8range``
    intervals into persisted rows and refreshes the region's cached summary.
    Two callers share it, differing only in where the per-tile probability
    density comes from:

    - :func:`bulk_create_tiles` -- ordinary geometry from a MOC, where every
      tile gets the *same* (usually ``None``) density.
    - :func:`tom_regions.services.skymap.ingest_skymap` -- a probability skymap,
      where each tile carries its *own* density.

    Args:
        region: The (already-saved) Region to attach tiles to.
        tiles: Iterable of ``(lower, upper, probdensity)``. ``lower``/``upper``
            are deepest-level NESTED indices forming a half-open
            ``[lower, upper)`` interval; ``probdensity`` is inverse steradians or
            ``None``.
        batch_size: Per-batch row count for ``bulk_create``.

    Returns:
        Number of tiles inserted.
    """
    from tom_regions.models import RegionTile

    rows = [
        RegionTile(region=region, hpx=NumericRange(lower, upper, "[)"), probdensity=probdensity)
        for lower, upper, probdensity in tiles
    ]
    RegionTile.objects.bulk_create(rows, batch_size=batch_size)
    recompute_region_summary(region)
    return len(rows)


def bulk_create_tiles(
    region: "Region",
    moc: "MOC",
    *,
    probdensity: float | None = None,
    batch_size: int = 1000,
) -> int:
    """Materialize a MOC into RegionTile rows for ``region``.

    Iterates ``moc.uniq_hpx``, converts each UNIQ to a deepest-level
    ``[lower, upper)`` interval, and bulk-inserts the rows. Returns the
    number of tiles inserted. Calls :func:`recompute_region_summary` at
    the end so the region's cached ``area_sr`` / ``n_tiles`` /
    ``max_depth`` reflect the new tile set.

    Args:
        region: The Region row to attach tiles to.
        moc: A :class:`mocpy.MOC`.
        probdensity: Optional probability density in inverse steradians,
            applied to *every* inserted tile. For a real probability skymap
            (a different density per tile) use
            :func:`tom_regions.services.skymap.ingest_skymap` instead.
        batch_size: Per-batch row count for ``bulk_create``. The default
            is conservative; LIGO-scale ingestions can usually go higher.

    Returns:
        Number of tiles inserted.
    """
    # One scalar density for the whole MOC: broadcast it across the tiles and
    # hand the triples to the shared materializer.
    return materialize_tiles(
        region,
        ((lower, upper, probdensity) for lower, upper in moc_to_ranges(moc)),
        batch_size=batch_size,
    )


def recompute_region_summary(region: "Region") -> None:
    """Refresh the region's cached geometric summary fields.

    Re-runs the aggregation queries that ``area_sr``, ``n_tiles``, and
    ``max_depth`` cache, then ``save()``s the updated columns. The
    centroid fields are not yet computed -- they will be populated in a
    later phase once we settle on the right metric (true sky-area
    centroid is non-trivial on a sphere; for many use cases the bounding
    pixel suffices).
    """
    from django.db.models import Count

    qs = region.tiles.all()
    aggregates = qs.aggregate(
        n_tiles=Count("id"),
        # Sum of ``upper - lower`` over all tiles, then multiplied by
        # PIXEL_AREA_STER, gives total area in steradians. We do the
        # subtraction Python-side after pulling the int rows back to
        # avoid a custom SQL function call here -- the alternative is a
        # raw expression with ``upper(hpx) - lower(hpx)`` which we'll
        # reach for once we have a use case for it.
    )
    n_tiles = aggregates["n_tiles"] or 0

    if n_tiles == 0:
        region.n_tiles = 0
        region.area_sr = None
        region.max_depth = None
        region.save(update_fields=["n_tiles", "area_sr", "max_depth", "modified"])
        return

    # Pull ``hpx`` back as NumericRange instances; sum ``upper - lower``
    # in Python. For Phase-1-scale tile counts (a few thousand) this is
    # cheaper than introducing a custom SQL aggregate. We can revisit if
    # a downstream TOM stores LIGO-scale skymaps with 100k+ tiles.
    total_pixels = 0
    deepest_shift = 2 * 29  # default = level 0 (largest tile, max shift)
    for tile in qs.only("hpx").iterator():
        lower = tile.hpx.lower
        upper = tile.hpx.upper
        total_pixels += upper - lower
        # range_to_level_ipix raises if a tile has a non-NESTED shape;
        # we only insert through bulk_create_tiles, which always emits
        # NESTED-aligned ranges, so a misaligned interval is a bug.
        level, _ipix = range_to_level_ipix(lower, upper)
        # Smaller shift -> deeper level. Track the minimum shift seen.
        shift = 2 * (29 - level)
        if shift < deepest_shift:
            deepest_shift = shift
    region.n_tiles = n_tiles
    region.area_sr = total_pixels * PIXEL_AREA_STER
    region.max_depth = 29 - deepest_shift // 2
    region.save(update_fields=["n_tiles", "area_sr", "max_depth", "modified"])


def region_to_moc_json(region: "Region") -> dict[str, list[int]]:
    """Serialize a region's tile set as an IVOA MOC JSON dict.

    Shape: ``{"<order>": [<ipix>, <ipix>, ...], ...}``. The dict has
    *one key per HEALPix order present in the tile set*, not a single
    flat key -- because the underlying RegionTile rows themselves span
    multiple orders. Each row's range length encodes its native level
    (see :func:`tom_regions.healpix_django.encoding.range_to_level_ipix`),
    so the loop below is just bucketing by that level.

    This is the wire format both ``mocpy.MOC.serialize(format='json')``
    and Aladin Lite v3's :js:func:`A.MOCFromJSON` consume. Two callers
    in tom_regions need the same dict:

    - :class:`tom_regions.views.RegionMOCJsonView` -- serves the dict
      as the body of an ``application/json`` response so external IVOA
      tools can fetch the URL directly.
    - :func:`tom_regions.templatetags.regions_extras.aladin_region_skymap`
      -- inlines the dict into the page payload so Aladin Lite can build
      the overlay synchronously without a follow-up fetch (the
      ``MOCFromURL`` path expects FITS, so JSON has to ride inline).

    Centralizing the encoding here keeps the two paths from drifting --
    a class of bug where the HTTP endpoint and the in-page overlay
    quietly disagree about ipix order or coalescing.
    """
    from collections import defaultdict

    from tom_regions.healpix_django.encoding import range_to_level_ipix

    moc_json: dict[str, list[int]] = defaultdict(list)
    for hpx in region.tiles.values_list("hpx", flat=True):
        # Tiles are always NESTED-aligned (they are inserted via
        # bulk_create_tiles, which only emits NESTED ranges), so the
        # decode is unambiguous.
        level, ipix = range_to_level_ipix(hpx.lower, hpx.upper)
        moc_json[str(level)].append(ipix)
    return dict(moc_json)


def region_to_moc(region: "Region") -> "MOC":
    """Reconstruct a :class:`mocpy.MOC` from a region's tile rows.

    Uses ``MOC.from_depth29_ranges`` -- the inverse of the encoding our
    rows already store, so no level-decoding round-trip is needed. Useful
    for serialization to FITS / JSON or for further mocpy operations
    (intersection, complement) that don't have a clean SQL equivalent.
    """
    import numpy as np
    from mocpy import MOC

    rows = list(region.tiles.values_list("hpx", flat=True))
    if not rows:
        return MOC.new_empty(max_depth=29)
    ranges = np.array(
        [[r.lower, r.upper] for r in rows], dtype=np.uint64
    )
    return MOC.from_depth29_ranges(max_depth=29, ranges=ranges)
