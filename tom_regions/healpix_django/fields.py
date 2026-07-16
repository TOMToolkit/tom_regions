"""Custom Django ORM fields for HEALPix points and tiles.

Two columns are introduced:

- :class:`HealpixPointField` -- stores a single sky position as the
  deepest-level NESTED HEALPix index (a ``bigint``). Used for things like
  "the location of one galaxy."

- :class:`HealpixTileField` -- stores a single MOC tile as a half-open
  interval of deepest-level pixel indices (an ``int8range``). Many rows of
  this column, joined by foreign key to a "region" table, encode a
  multi-resolution coverage map.

Each field subclasses an existing Django field rather than reinventing the
wheel: ``BigIntegerField`` already knows how to round-trip int64 values to
``bigint`` columns, and ``BigIntegerRangeField`` already knows how to
round-trip ``psycopg2.extras.NumericRange`` objects to ``int8range``. We
extend ``get_prep_value`` to accept the ergonomic Python forms users want
(``SkyCoord`` and UNIQ ints) and coerce them into whatever the parent
class expects.

Design note: input forms accepted
---------------------------------
For both fields we deliberately keep the accepted input set *small and
unambiguous* rather than try to be clever. Where a particular coordinate
form is needed, the caller converts explicitly via the helpers in
:mod:`tom_regions.healpix_django.encoding`.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.encoding` (the pure-Python
            conversions these fields delegate to).
- Next:     :mod:`tom_regions.healpix_django.lookups`, which describes
            which ORM lookups are available on these fields.
"""

from __future__ import annotations

from typing import Any

from django.contrib.postgres.fields import BigIntegerRangeField
from django.db import models

from .encoding import skycoord_to_point, uniq_to_range


class HealpixPointField(models.BigIntegerField):
    """A NESTED HEALPix pixel index at the deepest supported level.

    Accepts on input:

    - ``int`` -- treated as already-encoded deepest-level ``ipix``.
    - ``astropy.coordinates.SkyCoord`` -- encoded via
      :func:`tom_regions.healpix_django.encoding.skycoord_to_point`.
    - ``(ra_deg, dec_deg)`` 2-tuple of floats -- encoded as if a SkyCoord.
    - ``None`` -- if ``null=True``.

    Reads from the database as plain ``int``; converting back to a
    :class:`~astropy.coordinates.SkyCoord` is the caller's job (it's a tiny
    function that requires astropy, which we don't want to make a hard
    runtime dependency for every column read).
    """

    description = "Deepest-level HEALPix NESTED pixel index (int64)."

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        # Avoid a top-level astropy import: a hot Django boot path shouldn't
        # pay the cost of pulling in astropy just to define a field class.
        try:
            from astropy.coordinates import SkyCoord  # type: ignore[import-not-found]
        except ImportError:
            SkyCoord = None  # noqa: N806

        if SkyCoord is not None and isinstance(value, SkyCoord):
            return skycoord_to_point(value)
        if isinstance(value, tuple) and len(value) == 2:
            ra_deg, dec_deg = value
            if SkyCoord is None:
                raise TypeError(
                    "(ra, dec) tuple input requires astropy; install astropy "
                    "or pass a pre-encoded int instead."
                )
            return skycoord_to_point(SkyCoord(ra_deg, dec_deg, unit="deg"))
        raise TypeError(
            f"HealpixPointField cannot prep value of type {type(value).__name__}; "
            "expected int, SkyCoord, or (ra_deg, dec_deg) tuple."
        )


class HealpixTileField(BigIntegerRangeField):
    """A single MOC tile, stored as ``int8range`` of deepest-level indices.

    Accepts exactly three input forms; anything else raises ``TypeError``.
    The forms are listed in roughly decreasing order of how often they're
    used in practice:

    1. ``int`` -- treated as an IVOA UNIQ-encoded HEALPix pixel and
       expanded to a deepest-level interval via
       :func:`encoding.uniq_to_range`. This is what ``mocpy.MOC.uniq_hpx``
       emits, so it's the dominant ingest path.
    2. ``psycopg2.extras.NumericRange`` -- passed through unchanged.
       This is what reads from the database produce, so round-tripping a
       row in Python "just works."
    3. ``(lower, upper)`` 2-tuple of ints -- wrapped as a half-open
       ``[lower, upper)`` range. Convenient for tests and for hand-built
       intervals.

    For the ``(level, ipix)`` form, call
    :func:`tom_regions.healpix_django.encoding.level_ipix_to_range` first
    and pass the resulting ``(lower, upper)`` tuple. The conversion is
    one line and avoids the ambiguity between "(level, ipix)" and
    "(lower, upper)" that would otherwise have to be resolved by sentinel
    values. Clarity over cleverness.

    Reads from the database as ``psycopg2.extras.NumericRange`` (Django's
    default for range fields).
    """

    description = "HEALPix MOC tile as half-open int8range of deepest-level indices."

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return None

        # psycopg2 is a hard dependency of the package, but lazy-import to
        # avoid a top-level cost in the rare case the field is imported by a
        # tooling pipeline that doesn't actually run queries.
        from psycopg2.extras import NumericRange  # type: ignore[import-not-found]

        if isinstance(value, NumericRange):
            return super().get_prep_value(value)
        # Test ``bool`` first: ``isinstance(True, int)`` is True in Python and
        # we don't want a stray bool to be silently treated as a UNIQ.
        if isinstance(value, int) and not isinstance(value, bool):
            lower, upper = uniq_to_range(value)
            return super().get_prep_value(NumericRange(lower, upper, "[)"))
        if (
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in value)
        ):
            lower, upper = value
            if upper <= lower:
                raise ValueError(
                    f"tile upper ({upper}) must exceed lower ({lower})."
                )
            return super().get_prep_value(NumericRange(lower, upper, "[)"))
        raise TypeError(
            f"HealpixTileField cannot prep value of type {type(value).__name__}; "
            "expected NumericRange, int (UNIQ), or (lower, upper) tuple. "
            "For (level, ipix) input, call encoding.level_ipix_to_range() "
            "first and pass the result."
        )
