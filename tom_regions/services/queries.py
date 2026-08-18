"""Read selectors that answer "what falls inside this region?".

These take a region and return the targets (or observation records) whose sky
position lies within it. The core is deliberately model-agnostic so the same
code serves the list-page filter, a DRF endpoint, a management command, and
``tom_nonlocalizedevents``.

Two query strategies
--------------------
- **Bounded**: one indexed ``tiles__hpx__contains`` test per candidate target.
  The right tool for "score these few candidates" -- the targets already linked
  to a gravitational-wave event, say -- which is tens to low hundreds of rows,
  and it needs neither tom_targets nor any cached data.
- **Indexed whole-table**: join the ``TargetHealpix`` side table (each target's
  stored deepest-level HEALPix point) to the region's tiles in one set-based
  SQL statement, served by the SP-GiST tile index. This is how the whole
  Target table is scanned for membership without one query per row.

:func:`targets_in_region` picks between them by whether it is handed a
candidate set; :func:`target_ids_in_region` is the indexed core.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astropy.coordinates import SkyCoord

from tom_regions.healpix_django.encoding import skycoord_to_point

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tom_regions.base_models import BaseRegion


def region_contains_point(region: BaseRegion, ra: float, dec: float) -> bool:
    """Return whether the sky point ``(ra, dec)`` (degrees, ICRS) lies in ``region``.

    Computes the point's deepest-level HEALPix pixel and asks PostgreSQL whether
    any of the region's tiles contain it (``int8range @> bigint``, SP-GiST
    indexed). This single-point test is the primitive the queries below build on.
    """
    point = skycoord_to_point(SkyCoord(ra, dec, unit="deg"))
    return region.tiles.filter(hpx__contains=point).exists()


def targets_in_region(region: BaseRegion, targets: Iterable | None = None):
    """Return the targets whose sky position falls inside ``region``.

    Two modes, chosen by whether you hand it a candidate set:

    - ``targets=None`` (default) -- the **indexed whole-table** query. Joins the
      ``TargetHealpix`` side table to the region's tiles in one SQL statement
      (see :func:`target_ids_in_region`) and returns a ``Target`` **queryset**.
      Requires tom_targets and a populated ``TargetHealpix`` table (kept current
      by the Target ``post_save`` signal plus the ``backfill_target_healpix``
      command).
    - ``targets`` given -- the **bounded** path. Tests each candidate object
      (anything exposing ``ra``/``dec`` in degrees) with one indexed containment
      query and returns the matching candidates as a **list**, in input order.
      Right for "score these few candidates"; needs neither tom_targets nor the
      side table.

    Targets with no ``ra``/``dec`` (non-sidereal, or not yet resolved) are
    skipped -- they have no fixed sky pixel to test.
    """
    if targets is None:
        from tom_targets.models import Target

        return Target.objects.filter(id__in=target_ids_in_region(region))

    inside = []
    for target in targets:
        ra = getattr(target, "ra", None)
        dec = getattr(target, "dec", None)
        if ra is None or dec is None:
            continue
        if region_contains_point(region, ra, dec):
            inside.append(target)
    return inside


def target_ids_in_region(region: BaseRegion):
    """Return the ids of targets whose cached HEALPix point lies in ``region``.

    The indexed core of :func:`targets_in_region`. For each ``TargetHealpix`` row
    it asks whether any of the region's tiles contains that point
    (``regiontile.hpx @> targethealpix.hpx``), which the SP-GiST index on the
    tile column serves. Returns a ``target_id`` values-list queryset, so callers
    map it back to whatever Target model they use without this module importing
    tom_targets.
    """
    from django.db.models import Exists, OuterRef

    from tom_regions.models import TargetHealpix

    in_region = region.tiles.filter(hpx__contains=OuterRef("hpx"))
    return TargetHealpix.objects.filter(Exists(in_region)).values_list("target_id", flat=True)


def observation_records_in_region(region: BaseRegion):
    """Return the ``ObservationRecord`` queryset for observations inside ``region``.

    "Observations in a region" reduces to "observations of targets in the
    region": an :class:`~tom_observations.models.ObservationRecord` points at a
    single ``Target``, so we just follow that foreign key -- the indexed
    :func:`targets_in_region` plus a ``target__in`` filter. No new spatial code.
    """
    from tom_observations.models import ObservationRecord

    return ObservationRecord.objects.filter(target__in=targets_in_region(region))


def regions_containing_target(target):
    """Return the regions whose tiles cover ``target``'s position.

    The reverse of :func:`targets_in_region`, and what powers the "Regions" tab
    on the target detail page. Honors a swapped Region model via
    :func:`~tom_regions.base_models.get_region_model_class`. Returns an empty
    queryset for a target without coordinates.
    """
    from tom_regions.base_models import get_region_model_class

    region_model = get_region_model_class()
    ra = getattr(target, "ra", None)
    dec = getattr(target, "dec", None)
    if ra is None or dec is None:
        return region_model.objects.none()
    point = skycoord_to_point(SkyCoord(ra, dec, unit="deg"))
    return region_model.objects.filter(tiles__hpx__contains=point).distinct()
