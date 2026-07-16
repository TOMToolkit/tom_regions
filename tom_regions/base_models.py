"""Abstract base classes for the Region object graph.

The shape of a "region" in tom_regions is intentionally close to the shape
of a "target" in tom_targets: a name, a type, a creator, some cached
geometric summary, and a hook for downstream TOMs to subclass and add
domain-specific fields. The customization mechanism is **subclassing**, not
a key-value extras table -- TOMs that need extra columns subclass
:class:`BaseRegion` and point ``settings.REGION_MODEL_CLASS`` at the
subclass.

This module also provides :func:`get_region_model_class`, which resolves
the configured (or default) concrete model. Use it whenever you need the
"current" Region class, the way ``django.contrib.auth.get_user_model``
resolves the User class.

The actual concrete :class:`Region`, :class:`RegionTile`, :class:`RegionName`,
and :class:`RegionList` live in :mod:`tom_regions.models`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Type

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.urls import reverse


# Region.type is metadata about provenance only -- the underlying
# geometry lives in RegionTile rows regardless of how it got there.
# We deliberately keep the type set short: distinguishing every shape
# the user could have drawn in Aladin (cone vs rect vs polygon) is
# information the database doesn't need. The semantic distinction
# that *does* matter -- whether a tile carries a probability density
# (LIGO skymap-style) -- lives on RegionTile.probdensity, not here.
REGION_TYPE_ALADIN = "ALADIN"          # drawn in Aladin Lite
REGION_TYPE_FITS_MOC = "FITS_MOC"      # uploaded as a MOC FITS file
REGION_TYPE_OTHER = "OTHER"            # any other source (LIGO skymap, programmatic, ...)

REGION_TYPE_CHOICES = (
    (REGION_TYPE_ALADIN, "Aladin"),
    (REGION_TYPE_FITS_MOC, "MOC FITS"),
    (REGION_TYPE_OTHER, "Other"),
)


class BaseRegion(models.Model):
    """Abstract base for the Region model.

    Concrete subclasses (the default is :class:`tom_regions.models.Region`)
    add nothing beyond ``class Meta: swappable = "REGION_MODEL_CLASS"`` --
    they exist so a downstream TOM can swap in its own subclass without
    touching tom_regions itself. Mirrors the tom_targets ``BaseTarget`` /
    ``Target`` split.

    Geometry lives in :class:`tom_regions.models.RegionTile` (one row per
    HEALPix tile), not on the region itself. The fields here are *cached
    summaries* -- area, centroid, tile count, deepest order -- recomputed
    via :meth:`recompute_summary` whenever the tile set changes. The
    region is the "envelope"; the tiles are the geometry.
    """

    name = models.CharField(
        max_length=200,
        unique=True,
        help_text="Unique human-readable name. Aliases live in RegionName.",
    )
    type = models.CharField(
        max_length=20,
        choices=REGION_TYPE_CHOICES,
        default=REGION_TYPE_OTHER,
        help_text="How this region was constructed; informational, not enforced.",
    )
    description = models.TextField(blank=True, default="")

    # Record-keeping only in v1; not used for permission filtering.
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="regions",
    )
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True)

    # Cached scalar summaries of the tile set. Refreshed by
    # :meth:`recompute_summary` whenever bulk_create_tiles inserts rows.
    # All nullable because an empty region (no tiles yet) has no geometry.
    area_sr = models.FloatField(
        null=True,
        blank=True,
        help_text="Total solid angle in steradians.",
    )
    centroid_ra = models.FloatField(null=True, blank=True)
    centroid_dec = models.FloatField(null=True, blank=True)
    n_tiles = models.PositiveIntegerField(default=0)
    max_depth = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Deepest HEALPix order present in the tile set.",
    )

    class Meta:
        abstract = True
        ordering = ["-created"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("regions:detail", kwargs={"pk": self.pk})

    @property
    def area_sq_deg(self) -> float | None:
        """Cached area in square degrees, the user-facing unit.

        ``area_sr`` on the database is steradians (SI). Astronomers
        read sky areas in deg² nearly universally, so all UI surfaces
        (list table, detail card, Aladin popup, filter inputs) convert
        through this property. ``None`` for empty regions.
        """
        from tom_regions.healpix_django.constants import SQ_DEG_PER_STERADIAN

        if self.area_sr is None:
            return None
        return self.area_sr * SQ_DEG_PER_STERADIAN

    # ------------------------------------------------------------------
    # Geometry helpers. Implementations live in tom_regions.utils so they
    # can be reused by forms, serializers, and admin actions without
    # importing the model module.
    # ------------------------------------------------------------------

    def to_moc(self):
        """Materialize this region's tile set as a :class:`mocpy.MOC`.

        Lazy-imported helper; mocpy is a runtime dep but we don't want
        every model load to pay its import cost.
        """
        from tom_regions.utils import region_to_moc

        return region_to_moc(self)

    def recompute_summary(self) -> None:
        """Refresh ``area_sr`` / ``n_tiles`` / ``max_depth`` from the tile set.

        Called from form/serializer ``save`` methods after bulk-inserting
        tiles. Intentionally explicit (not a signal) because
        ``bulk_create`` does not fire ``post_save`` -- a signal would
        silently miss the only path that actually writes tiles.
        """
        from tom_regions.utils import recompute_region_summary

        recompute_region_summary(self)


# ---------------------------------------------------------------------------
# Swappable-model resolver. Mirrors ``tom_targets.base_models.get_target_model_class``.
# ---------------------------------------------------------------------------

DEFAULT_REGION_MODEL_CLASS = "tom_regions.models.Region"


def get_region_model_class() -> Type[BaseRegion]:
    """Return the currently configured concrete Region model class.

    Resolves ``settings.REGION_MODEL_CLASS`` (a dotted path) and imports
    it on demand. Falls back to the default ``tom_regions.models.Region``.

    Use this at call sites that need the model class but want to honor
    downstream TOMs' subclassing -- mirrors how Django's auth system
    uses ``get_user_model()``.

    Raises:
        ImportError: If the configured dotted path doesn't resolve.
        TypeError: If the resolved class is not a BaseRegion subclass.
    """
    dotted = getattr(settings, "REGION_MODEL_CLASS", DEFAULT_REGION_MODEL_CLASS)
    module_path, _, class_name = dotted.rpartition(".")
    if not module_path:
        raise ImportError(
            f"REGION_MODEL_CLASS={dotted!r} must be a dotted path like "
            "'tom_regions.models.Region'."
        )
    module = import_module(module_path)
    cls = getattr(module, class_name)
    if not (isinstance(cls, type) and issubclass(cls, BaseRegion)):
        raise TypeError(
            f"REGION_MODEL_CLASS={dotted!r} must resolve to a subclass of "
            "tom_regions.base_models.BaseRegion."
        )
    return cls


# Quiet a possible "AUTH_USER_MODEL is not yet loaded" warning from older
# linters: settings.AUTH_USER_MODEL is the documented Django pattern for
# FKs on abstract models (vs. ``get_user_model()``, which evaluates eagerly).
_ = get_user_model
