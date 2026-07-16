"""ORM lookups available on HEALPix tile fields.

Django's :class:`django.contrib.postgres.fields.RangeField` already provides
the lookups we need:

- ``__overlap`` (``&&``) -- "do these two tiles share any deepest-level pixel?"
- ``__contains`` (``@>``) -- "does this tile contain that point or that tile?"
- ``__contained_by`` (``<@``) -- "is this tile inside that tile?"

Those are inherited automatically by :class:`HealpixTileField`. We don't need
to register anything new for v1. This module exists as a deliberate
placeholder, so future custom lookups land in an obvious place and
``__init__.py`` can register them by importing this module.

If a future caller hits friction with ``__contains`` against a
:class:`HealpixPointField` right-hand-side, the fix is to register a small
:class:`Lookup` subclass that emits ``range @> bigint`` against the
``HealpixTileField`` left-hand-side. The shape would be::

    @HealpixTileField.register_lookup
    class TileContainsPoint(Lookup):
        lookup_name = "contains_point"
        def as_sql(self, compiler, connection):
            lhs, lhs_params = self.process_lhs(compiler, connection)
            rhs, rhs_params = self.process_rhs(compiler, connection)
            return f"{lhs} @> {rhs}::bigint", lhs_params + rhs_params

But we won't pre-emptively add it. PostgreSQL's overload resolution makes
the inherited ``__contains`` work for both the int and range RHS forms.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.fields` (the field classes
            these lookups are registered against).
- Next:     :mod:`tom_regions.healpix_django.functions`, which provides
            SQL-side arithmetic on tiles for queries that go beyond a
            single-predicate filter.
"""

from __future__ import annotations
