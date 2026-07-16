"""Index helpers for HEALPix tile columns.

PostgreSQL ships two index types that work well with range columns: GiST
and SP-GiST. For ``int8range`` columns, SP-GiST tends to win on both build
time and storage size for typical MOC workloads. The HEALPix-Alchemy paper
section 3.3 walks through the trade-offs in more depth.

Django 5.x exposes :class:`django.contrib.postgres.indexes.SpGistIndex`,
which emits ``CREATE INDEX ... USING spgist (column)``. The default opclass
for ``int8range`` is ``range_ops``, which is what we want, so the wrapper
here is intentionally thin -- it exists primarily as a named symbol the
plan refers to and as the natural place to add per-column tuning if it
becomes necessary later.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.functions` (the SQL operators
            this index accelerates).
- Next:     :mod:`tom_regions.healpix_django.models`, which bundles a
            tile column and this index into an abstract base class so
            downstream models inherit both at once.
"""

from __future__ import annotations

from django.contrib.postgres.indexes import SpGistIndex


class HealpixSpGistIndex(SpGistIndex):
    """SP-GiST index over a :class:`HealpixTileField`.

    Use via :class:`tom_regions.healpix_django.MOCMixin`, which adds it to
    ``Meta.indexes`` automatically. Direct usage looks like::

        class FieldTile(MOCMixin):
            field = ForeignKey(Field, on_delete=CASCADE)

            class Meta(MOCMixin.Meta):
                indexes = [
                    HealpixSpGistIndex(fields=["hpx"]),
                    Index(fields=["field"]),
                ]
    """
