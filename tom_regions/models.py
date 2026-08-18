"""Concrete region models for tom_regions.

The abstract :class:`tom_regions.base_models.BaseRegion` defines the
field shape; this module declares the default concrete subclass and the
related "satellite" tables (tile rows, aliases, region groups). Downstream
TOMs that want extra columns subclass :class:`BaseRegion` directly and
point ``settings.REGION_MODEL_CLASS`` at their subclass.
"""

from __future__ import annotations

from django.db import models

from tom_regions.base_models import BaseRegion
from tom_regions.healpix_django import HealpixPointField, MOCMixin


class Region(BaseRegion):
    """Default concrete Region model.

    Adds nothing of its own; it exists as a distinct class so that
    downstream TOMs subclass :class:`BaseRegion` and select via
    ``settings.REGION_MODEL_CLASS`` (resolved by
    :func:`tom_regions.base_models.get_region_model_class`).

    Phase 1 deliberately omits ``Meta.swappable``: setting it is the
    right move once we ship a default for ``REGION_MODEL_CLASS`` (likely
    via a tom_regions ``default_settings`` module imported by host
    TOMs), but introducing the option without that default forces every
    consumer to set the setting themselves. We'd rather defer that
    coordination to Phase 2.
    """

    class Meta(BaseRegion.Meta):
        pass


class RegionName(models.Model):
    """Alternative human-readable name for a region.

    A region has one canonical ``name`` (unique across the table); any
    additional aliases live here. Searching by alias is the same as
    searching by name from the user's perspective.
    """

    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, related_name="aliases"
    )
    name = models.CharField(max_length=200, unique=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RegionList(models.Model):
    """A user-defined named collection of regions, like TargetList."""

    name = models.CharField(max_length=200, unique=True)
    regions = models.ManyToManyField(Region, related_name="region_lists", blank=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class RegionTile(MOCMixin):
    """One HEALPix tile of a region, stored as an ``int8range``.

    A region's full geometry is the union of its ``RegionTile`` rows. The
    ``hpx`` column comes from :class:`MOCMixin` (which also adds the
    SP-GiST index); the foreign key to :class:`Region` and the optional
    ``probdensity`` are added here.

    ``probdensity`` is ``null`` for ordinary regions (polygons, circles,
    field footprints) and populated only for SKYMAP-type regions (e.g.,
    LIGO localization maps), where each tile carries a probability
    density in inverse steradians. Keeping the column nullable from v1
    means LIGO ingest in Phase 4 needs no schema change -- it just
    populates the field on insert.
    """

    region = models.ForeignKey(
        Region, on_delete=models.CASCADE, related_name="tiles"
    )
    probdensity = models.FloatField(
        null=True,
        blank=True,
        help_text="Probability density in inverse steradians; non-null for skymap tiles.",
    )

    class Meta(MOCMixin.Meta):
        # MOCMixin contributes the SP-GiST index over ``hpx``. Django
        # auto-indexes the ``region`` FK column, so no explicit index on
        # that side is needed.
        indexes = MOCMixin.Meta.indexes

    def __str__(self) -> str:
        return f"{self.region.name} tile {self.pk}"


class TargetHealpix(models.Model):
    """Cached deepest-level HEALPix point of a Target, for fast region membership.

    A *side table* -- not a column on Target -- so tom_regions stays a clean
    plugin that never has to swap or migrate the core Target model. One row per
    sidereal target that has coordinates; maintained by ``post_save`` /
    ``post_delete`` receivers on Target (connected in
    :meth:`tom_regions.apps.TomRegionsConfig.ready` when tom_targets is
    installed) plus the ``backfill_target_healpix`` management command for
    targets loaded in bulk (which bypass signals).

    ``target_id`` is a plain integer, **not** a ``ForeignKey``: a hard FK would
    make tom_regions' schema require tom_targets to be installed, which breaks
    the standalone test boot and the "geometry works without tom_targets"
    design. Referential cleanup is the ``post_delete`` receiver's job (and a
    reconciling ``backfill`` run).

    The ``hpx`` column carries a plain B-tree index (``db_index=True``), not
    SP-GiST: a region tile is a *contiguous* int8range on the deepest-level
    integer axis, so "the points inside this tile" is a B-tree range scan -- the
    join shape :func:`tom_regions.services.queries.target_ids_in_region` relies
    on.
    """

    target_id = models.BigIntegerField(
        unique=True,
        help_text="Primary key of the Target this point caches (a plain id, not a FK, by design).",
    )
    hpx = HealpixPointField(
        db_index=True,
        help_text="The target's position as a deepest-level NESTED HEALPix pixel index.",
    )
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "target HEALPix point"
        verbose_name_plural = "target HEALPix points"

    def __str__(self) -> str:
        return f"target {self.target_id} @ hpx {self.hpx}"
