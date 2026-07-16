"""Abstract Django model bundling a tile column with its SP-GiST index.

The HEALPix-Alchemy data layout is "one row per MOC tile, with a foreign
key back to the parent region." That table has a fixed shape -- a
:class:`HealpixTileField` plus an SP-GiST index over it -- and downstream
models nearly always also want a foreign key, possibly a ``probdensity``
float, and maybe a couple of extra metadata columns. We provide the fixed
shape as an abstract base.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.indexes` (the SP-GiST index
            this mixin attaches automatically).
- Next:     :mod:`tom_regions.healpix_django.checks`, the runtime guard
            that ensures the database backend can actually execute the
            queries this mixin enables.
"""

from __future__ import annotations

from django.db import models

from .fields import HealpixTileField
from .indexes import HealpixSpGistIndex


class MOCMixin(models.Model):
    """Abstract base for "one row per HEALPix tile" tables.

    Concrete subclasses add a foreign key to their parent region, plus any
    per-tile columns (e.g., a probability density for skymap tiles).

    Example::

        from tom_regions.healpix_django import MOCMixin
        from django.db import models

        class RegionTile(MOCMixin):
            region = models.ForeignKey(
                "tom_regions.Region",
                on_delete=models.CASCADE,
                related_name="tiles",
            )
            probdensity = models.FloatField(null=True, blank=True)

            class Meta(MOCMixin.Meta):
                indexes = MOCMixin.Meta.indexes + [
                    models.Index(fields=["region"]),
                ]
    """

    hpx = HealpixTileField()

    class Meta:
        abstract = True
        indexes = [HealpixSpGistIndex(fields=["hpx"])]
