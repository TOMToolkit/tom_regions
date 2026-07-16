"""SQL functions and aggregates for HEALPix tile arithmetic.

Three operations are exposed:

- :class:`TileIntersect` -- the geometric intersection of two tiles. SQL
  emits PostgreSQL's binary ``*`` operator on ``int8range``, returning a
  (possibly empty) range. Useful for "what is the overlap between this
  field and that skymap pixel?" queries.

- :class:`TileArea` -- the solid angle subtended by a tile, in steradians.
  SQL computes ``(upper(t) - lower(t)) * PIXEL_AREA_STER``. Multiply by
  per-tile probability density to get integrated probability.

- :class:`TileUnion` -- aggregate combining many tile rows into a flat
  list of disjoint tiles. SQL is ``unnest(range_agg(t))`` (PostgreSQL 14+),
  returning one row per disjoint range. Mirrors the upstream
  ``healpix_alchemy.func.union``.

The HEALPix-Alchemy paper's headline queries -- "galaxies in a credible
region", "fields ranked by skymap probability", "total area covered by a
list of pointings" -- all reduce to combinations of these three
expressions plus a join.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.lookups` (single-predicate
            filters on tile columns).
- Next:     :mod:`tom_regions.healpix_django.indexes`, which makes the
            queries above scale by adding an SP-GiST index over each tile
            column.
"""

from __future__ import annotations

from django.contrib.postgres.fields import BigIntegerRangeField
from django.db.models import Aggregate, FloatField, Func

from .constants import PIXEL_AREA_STER


class TileIntersect(Func):
    """SQL: ``a * b`` between two ``int8range`` values; returns ``int8range``.

    Result is the geometric intersection of two tiles, as a (possibly empty)
    range. Use ``Q(hpx__overlap=...)`` if you only need a boolean overlap
    test -- ``TileIntersect`` is for cases where you also need the
    intersected range itself (typically because you'll multiply its area
    against a probability density).
    """

    arg_joiner = " * "
    template = "(%(expressions)s)"
    function = ""  # operator-style; see template
    output_field = BigIntegerRangeField()


class TileArea(Func):
    """SQL: ``(upper(t) - lower(t)) * PIXEL_AREA_STER``; returns ``double precision``.

    Computes the solid angle of a tile (or the result of :class:`TileIntersect`)
    in steradians. The constant :data:`PIXEL_AREA_STER` is baked into the SQL
    rather than being a parameter so query plans cache cleanly.
    """

    template = (
        "((upper(%(expressions)s) - lower(%(expressions)s))::double precision "
        "* %(pixel_area)s)"
    )
    function = ""
    output_field = FloatField()

    def __init__(self, expression, **extra):
        super().__init__(expression, pixel_area=repr(PIXEL_AREA_STER), **extra)


class TileUnion(Aggregate):
    """SQL aggregate: ``unnest(range_agg(t))``; returns rows of ``int8range``.

    Combines many tile rows into a flat sequence of disjoint deepest-level
    intervals. PostgreSQL's ``range_agg`` produces an ``int8multirange``
    (one column, many ranges packed inside); we follow that with ``unnest``
    so the result is back to a row-set of ``int8range`` values that's
    identical in shape to the input table -- which lets the caller treat
    the union output as just another tile column for downstream queries.

    Requires PostgreSQL >= 14 (multirange + ``range_agg``). Enforced at
    app-startup time by :func:`tom_regions.healpix_django.checks.assert_postgres_supported`.
    """

    function = "range_agg"
    name = "TileUnion"
    template = "unnest(%(function)s(%(expressions)s))"
    output_field = BigIntegerRangeField()
