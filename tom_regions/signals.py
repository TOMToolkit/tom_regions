"""Signal handlers that keep the ``TargetHealpix`` cache in sync.

tom_regions caches each sidereal target's deepest-level HEALPix point in
:class:`~tom_regions.models.TargetHealpix` so that "targets in a region" is a
single indexed join
(:func:`tom_regions.services.queries.target_ids_in_region`) rather than one
query per target. These receivers maintain that cache as targets are created,
edited, or deleted.

They are connected in :meth:`tom_regions.apps.TomRegionsConfig.ready` -- and only
when tom_targets is installed, so a project (or the standalone test boot) without
it is unaffected.

Caveat: ``bulk_create`` / ``bulk_update`` / ``QuerySet.update`` do **not** emit
``post_save`` / ``post_delete``. After a bulk load run ``backfill_target_healpix``
to reconcile; it is the source of truth, while these signals are the incremental
fast-path for interactive edits.
"""

from __future__ import annotations

from typing import Any


def update_target_healpix(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Upsert the cached HEALPix point for a saved target.

    A sidereal target with coordinates gets (or refreshes) its row. A target that
    lacks ``ra``/``dec`` -- non-sidereal, or coordinates cleared -- has any stale
    row removed, so the cache never points at a missing position.
    """
    from astropy.coordinates import SkyCoord

    from tom_regions.healpix_django.encoding import skycoord_to_point
    from tom_regions.models import TargetHealpix

    ra = getattr(instance, "ra", None)
    dec = getattr(instance, "dec", None)
    if ra is None or dec is None:
        TargetHealpix.objects.filter(target_id=instance.id).delete()
        return
    point = skycoord_to_point(SkyCoord(ra, dec, unit="deg"))
    TargetHealpix.objects.update_or_create(target_id=instance.id, defaults={"hpx": point})


def delete_target_healpix(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Drop the cached HEALPix point when its target is deleted."""
    from tom_regions.models import TargetHealpix

    TargetHealpix.objects.filter(target_id=instance.id).delete()
