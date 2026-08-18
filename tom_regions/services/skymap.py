"""Probability-skymap operations for tom_regions.

A *probability skymap* is a multi-order HEALPix map -- LIGO/Virgo/KAGRA
localizations are the canonical example -- where every tile carries a
``PROBDENSITY`` (a probability density in inverse steradians) such that
``probdensity * tile_area`` integrates to 1 over the sphere. This module ingests
such a map into a region's tiles, derives credible-region contours from it, and
scores how deep into the credible region a given sky point sits.

Where the pieces come from
--------------------------
- Per-tile geometry + density storage is just ``RegionTile`` rows with
  ``probdensity`` populated; we reuse :func:`tom_regions.utils.materialize_tiles`.
- Each credible *contour* (the smallest area holding X% of the probability) is a
  single vectorized ``mocpy`` call -- ``MOC.from_valued_healpix_cells(...,
  cumul_to=X)`` -- so we never hand-roll the cumulative-probability loop.
- Scoring a point reuses the SP-GiST ``__contains`` lookup plus the
  :class:`~tom_regions.healpix_django.functions.TileArea` SQL expression.

The contour levels are a deployment setting; see :func:`credible_probabilities`.
"""

from __future__ import annotations

import json
import sys
from math import pi
from typing import TYPE_CHECKING

from tom_regions.base_models import REGION_TYPE_OTHER
from tom_regions.healpix_django.constants import SQ_DEG_PER_STERADIAN
from tom_regions.healpix_django.encoding import (
    skycoord_to_point,
    uniq_to_level_ipix,
    uniq_to_range,
)
from tom_regions.utils import materialize_tiles

if TYPE_CHECKING:
    from astropy.table import Table
    from mocpy import MOC

    from tom_regions.base_models import BaseRegion


# Default credible-region contour levels, mirroring tom_nonlocalizedevents'
# historical default. A deployment overrides this with a Django setting; the
# canonical way to drive that setting from the environment is, in settings.py::
#
#     # Override via the CREDIBLE_REGION_PROBABILITIES env var, e.g.
#     #   CREDIBLE_REGION_PROBABILITIES='[0.25, 0.5, 0.9]'
#     _crp = os.getenv('CREDIBLE_REGION_PROBABILITIES', [0.25, 0.5, 0.75, 0.9, 0.95])
#     CREDIBLE_REGION_PROBABILITIES = json.loads(_crp) if isinstance(_crp, str) else _crp
DEFAULT_CREDIBLE_REGION_PROBABILITIES = [0.25, 0.5, 0.75, 0.9, 0.95]

# Whole sky in square degrees (4*pi steradians). A MOC's ``sky_fraction`` times
# this is its area in the unit astronomers read.
_WHOLE_SKY_DEG2 = 4.0 * pi * SQ_DEG_PER_STERADIAN


def credible_probabilities() -> list[float]:
    """Return the configured credible-region contour levels, descending.

    Reads ``settings.CREDIBLE_REGION_PROBABILITIES`` -- the same name
    tom_nonlocalizedevents uses, so a deployment's existing override carries
    over -- and falls back to :data:`DEFAULT_CREDIBLE_REGION_PROBABILITIES`.
    Accepts either a Python list or a JSON string (the latter is what you get if
    a settings module read the value straight from an environment variable
    without parsing it).
    """
    from django.conf import settings

    probs = getattr(
        settings, "CREDIBLE_REGION_PROBABILITIES", DEFAULT_CREDIBLE_REGION_PROBABILITIES
    )
    if isinstance(probs, str):
        probs = json.loads(probs)
    # Descending so the largest region is computed first; callers that bucket a
    # point into its "smallest containing contour" rely on this ordering.
    return sorted((float(p) for p in probs), reverse=True)


def ingest_skymap(region: BaseRegion, table: Table, *, batch_size: int = 2000) -> int:
    """Populate ``region``'s tiles from a multi-order skymap ``table``.

    Reads the ``UNIQ`` and ``PROBDENSITY`` columns (the LIGO multi-order-map
    convention), converts each UNIQ cell to its deepest-level ``[lower, upper)``
    interval, and inserts one ``RegionTile`` per cell carrying its own
    ``probdensity``.

    Args:
        region: An already-saved region to attach the skymap tiles to.
        table: An :class:`astropy.table.Table` with ``UNIQ`` and ``PROBDENSITY``
            columns (e.g. from
            ``astropy.table.Table.read('<event>.multiorder.fits')``).
        batch_size: Per-batch row count for the bulk insert.

    Returns:
        Number of tiles inserted.
    """
    import numpy as np

    uniq = np.asarray(table["UNIQ"], dtype=np.uint64)
    density = np.asarray(table["PROBDENSITY"], dtype=float)

    # Float8 underflow guard: ``probdensity * area`` is evaluated in PostgreSQL
    # double precision, where densities below the smallest normal float would
    # silently flush to zero. Clamp them here so the stored value and the SQL
    # arithmetic agree. (tom_nonlocalizedevents carries the same guard.)
    density = np.where(density < sys.float_info.min, 0.0, density)

    triples = (
        (lower, upper, float(pd))
        for (lower, upper), pd in zip((uniq_to_range(int(u)) for u in uniq), density)
    )
    inserted = materialize_tiles(region, triples, batch_size=batch_size)

    # The presence of ``probdensity`` is what actually marks this a skymap; the
    # type is just provenance metadata for the UI. Record it without inventing a
    # new choice value.
    if region.type != REGION_TYPE_OTHER:
        region.type = REGION_TYPE_OTHER
        region.save(update_fields=["type", "modified"])
    return inserted


def ingest_multiorder_skymap(region: BaseRegion, fits_source, *, batch_size: int = 2000) -> int:
    """Read a multi-order skymap FITS and ingest it into ``region``.

    Convenience wrapper over :func:`ingest_skymap` that reads ``fits_source`` (a
    path, file-like object, or anything :meth:`astropy.table.Table.read`
    accepts) into a table first.
    """
    from astropy.table import Table

    return ingest_skymap(region, Table.read(fits_source), batch_size=batch_size)


def credible_region_moc(table: Table, prob: float) -> MOC:
    """Return the ``prob``-credible region of a skymap as a :class:`mocpy.MOC`.

    The credible region is the *smallest* set of cells whose summed probability
    reaches ``prob`` (cells sorted by density, accumulated from the top). mocpy
    does this in one vectorized call; we feed it the densities and a cumulative
    cutoff.

    Args:
        table: A multi-order skymap table (``UNIQ`` + ``PROBDENSITY``).
        prob: Credible level in ``(0, 1]`` -- e.g. ``0.9`` for the 90% region.
    """
    import numpy as np
    from mocpy import MOC

    uniq = np.asarray(table["UNIQ"], dtype=np.uint64)
    density = np.asarray(table["PROBDENSITY"], dtype=float)
    # UNIQ values are monotonic in level (level k occupies [4**(k+1), 4**(k+2))),
    # so the largest UNIQ has the deepest level -- the resolution we let the
    # contour reach.
    max_depth = uniq_to_level_ipix(int(uniq.max()))[0]
    return MOC.from_valued_healpix_cells(
        uniq, density, max_depth=max_depth, values_are_densities=True, cumul_to=float(prob)
    )


def credible_contours(
    table: Table, probabilities: list[float] | None = None
) -> dict[float, tuple[MOC, float]]:
    """Compute every credible-region contour once, keyed by probability.

    Args:
        table: A multi-order skymap table.
        probabilities: Credible levels to compute. Defaults to
            :func:`credible_probabilities` (the ``CREDIBLE_REGION_PROBABILITIES``
            setting).

    Returns:
        ``{prob: (moc, area_deg2)}`` -- the contour MOC and its area in square
        degrees -- for each requested level. Computing all contours here, once
        per localization, is what lets callers cache per-level areas instead of
        re-deriving them on every request.
    """
    if probabilities is None:
        probabilities = credible_probabilities()
    contours: dict[float, tuple[MOC, float]] = {}
    for prob in probabilities:
        moc = credible_region_moc(table, prob)
        contours[prob] = (moc, moc.sky_fraction * _WHOLE_SKY_DEG2)
    return contours


def probability_at_point(region: BaseRegion, skycoord) -> float | None:
    """Return the credible probability enclosed at ``skycoord`` within ``region``.

    For a probability skymap, a sky point's "smallest credible region" is the
    cumulative probability of every tile at least as dense as the tile the point
    lands in. That single number says, e.g., that a galaxy sits inside the 63%
    credible region.

    Args:
        region: A skymap region (its tiles carry ``probdensity``).
        skycoord: A scalar :class:`astropy.coordinates.SkyCoord`.

    Returns:
        The enclosed probability in ``[0, 1]``, or ``None`` if the point lies
        outside the region or the region is not a probability skymap (its tiles
        have ``probdensity`` of ``NULL``).
    """
    from django.db.models import F, Sum

    from tom_regions.healpix_django.functions import TileArea

    point = skycoord_to_point(skycoord)
    # The point lands in at most one tile (tiles are disjoint). That tile's
    # density is the threshold: every tile at least this dense is "inside" the
    # credible region before the point is.
    threshold = (
        region.tiles.filter(hpx__contains=point)
        .values_list("probdensity", flat=True)
        .first()
    )
    if threshold is None:
        # Point outside the region, or a non-skymap tile (probdensity is NULL).
        return None
    enclosed = region.tiles.filter(probdensity__gte=threshold).aggregate(
        p=Sum(F("probdensity") * TileArea(F("hpx")))
    )["p"]
    return enclosed
