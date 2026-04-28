"""Pure-Python encoding helpers for HEALPix tile representations.

Everything in this module is plain Python -- no Django imports, no database
calls, no third-party dependencies for the integer-encoding helpers. The only
function that reaches outside the standard library is :func:`skycoord_to_point`
(which uses ``cdshealpix``) and :func:`moc_to_ranges` (which uses ``mocpy``).
Both perform a lazy import so the rest of the module is usable even if those
optional dependencies aren't installed (e.g. for running the encoding unit
tests on a clean Python install).

The four core encodings
-----------------------
1.  **(level, ipix)** -- a HEALPix NESTED pixel at any order ``0..LEVEL``.
2.  **UNIQ** -- a single 64-bit integer encoding both order and pixel index,
    defined by IVOA MOC: ``uniq = 4 * 4**level + ipix``. This is the form
    in which mocpy and Aladin transmit MOCs.
3.  **(lower, upper)** -- a half-open ``int8range`` of deepest-level pixel
    indices. This is the canonical *database* representation.
4.  **(skycoord) -> ipix at LEVEL** -- a single point on the sphere maps to
    one deepest-level pixel index, stored as ``bigint`` in the database.

These four representations are pairwise convertible and round-trip cleanly.
The encoding tests (``tests/test_encoding.py``) exercise every conversion as
a worked example so a reader can build the same intuition that Leo Singer's
 HEALPix-Alchemy paper provides.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.constants` (the geometric
            constants the conversions below depend on).
- Next:     :mod:`tom_regions.healpix_django.fields`, which wraps the
            ``int``/``(lower, upper)`` representations into Django Field
            classes so they can live in a database column.
"""

from __future__ import annotations

from typing import Iterable, Iterator, Tuple

from .constants import LEVEL, shift_for_level


def uniq_to_level_ipix(uniq: int) -> Tuple[int, int]:
    """Decode an IVOA UNIQ index into ``(level, ipix)``.

    The UNIQ encoding packs the HEALPix order and the NESTED pixel index of
    that order into a single positive integer::

        uniq = 4 * (4**level) + ipix      with 0 <= ipix < 12 * 4**level

    Recovery uses the position of the leading set bit pair: the integer
    ``log4(uniq // 4)`` recovers the level. Equivalently we shift right
    until ``uniq`` falls below the level-0 bound (``4 * 4**0 = 4 + 12 = 16``,
    but the more uniform invariant is ``uniq // 4 >= 4**level``).

    Args:
        uniq: A positive UNIQ-encoded HEALPix index.

    Returns:
        ``(level, ipix)`` with ``0 <= level <= LEVEL`` and
        ``0 <= ipix < 12 * 4**level``.

    Raises:
        ValueError: If ``uniq`` is not positive or decodes to a level beyond
            the supported maximum :data:`LEVEL`.
    """
    if uniq < 4:
        raise ValueError(f"uniq must be >= 4 (encodes level 0), got {uniq!r}")
    # The smallest UNIQ at level k is 4 * 4**k = 4**(k+1). bit_length() of an
    # exact power of 4 is 2k + 3, so:
    #   bit_length(4**(k+1)) = 2(k+1) + 1 = 2k + 3
    # This gives the level by: level = (bit_length(uniq) - 1) // 2 - 1.
    level = (uniq.bit_length() - 1) // 2 - 1
    if level > LEVEL:
        raise ValueError(f"uniq encodes level {level} > LEVEL={LEVEL}")
    ipix = uniq - (4 << (2 * level))  # subtract 4 * 4**level
    return level, ipix


def level_ipix_to_uniq(level: int, ipix: int) -> int:
    """Encode ``(level, ipix)`` as an IVOA UNIQ integer.

    Args:
        level: HEALPix order in ``[0, LEVEL]``.
        ipix: NESTED pixel index at ``level``; must satisfy
            ``0 <= ipix < 12 * 4**level``.

    Returns:
        The UNIQ-encoded integer ``4 * 4**level + ipix``.

    Raises:
        ValueError: If ``level`` is out of range or ``ipix`` is out of range
            for ``level``.
    """
    if not 0 <= level <= LEVEL:
        raise ValueError(f"level must be in [0, {LEVEL}], got {level!r}")
    npix_at_level = 12 << (2 * level)  # 12 * 4**level
    if not 0 <= ipix < npix_at_level:
        raise ValueError(
            f"ipix must be in [0, {npix_at_level}) for level={level}, got {ipix!r}"
        )
    return (4 << (2 * level)) + ipix


def level_ipix_to_range(level: int, ipix: int) -> Tuple[int, int]:
    """Expand a NESTED pixel ``(level, ipix)`` into a deepest-level interval.

    A NESTED pixel at order ``k`` corresponds to a contiguous block of
    ``4**(LEVEL - k)`` pixels at the deepest order :data:`LEVEL`. This is the
    central insight that makes the whole int8range scheme work: a heterogeneous
    multi-order coverage map can be losslessly represented as a set of
    *disjoint* intervals over a single integer axis.

    Args:
        level: HEALPix order in ``[0, LEVEL]``.
        ipix: NESTED pixel index at ``level``.

    Returns:
        ``(lower, upper)`` half-open interval; in PostgreSQL ``int8range``
        notation that is ``[lower, upper)``.

    Raises:
        ValueError: If ``level`` or ``ipix`` is out of range.
    """
    npix_at_level = 12 << (2 * level)
    if not 0 <= ipix < npix_at_level:
        raise ValueError(
            f"ipix must be in [0, {npix_at_level}) for level={level}, got {ipix!r}"
        )
    shift = shift_for_level(level)  # validates level
    lower = ipix << shift
    upper = (ipix + 1) << shift
    return lower, upper


def range_to_level_ipix(lower: int, upper: int) -> Tuple[int, int]:
    """Recover ``(level, ipix)`` from a deepest-level interval.

    Inverse of :func:`level_ipix_to_range`. Only intervals produced by that
    function (or any other source aligned to a NESTED block boundary) decode
    cleanly; arbitrary intervals raise.

    Args:
        lower: Inclusive lower bound of the interval.
        upper: Exclusive upper bound. Must satisfy ``upper > lower``.

    Returns:
        ``(level, ipix)``.

    Raises:
        ValueError: If the interval doesn't correspond to a single NESTED
            pixel at any supported level (e.g., spans multiple sibling pixels,
            isn't a power-of-4 length, or isn't aligned to its own length).
    """
    if upper <= lower:
        raise ValueError(f"upper ({upper}) must exceed lower ({lower})")
    length = upper - lower
    # length must be 4**(LEVEL - level), which is a power of 4 in [1, 4**LEVEL].
    if length & (length - 1):
        raise ValueError(f"interval length {length} is not a power of 2")
    log2_length = length.bit_length() - 1
    if log2_length & 1:
        raise ValueError(f"interval length {length} is not a power of 4")
    shift = log2_length
    level = LEVEL - (shift // 2)
    if not 0 <= level <= LEVEL:
        raise ValueError(f"interval encodes level {level} outside [0, {LEVEL}]")
    if lower & (length - 1):
        raise ValueError(
            f"interval lower bound {lower} is not aligned to length {length}"
        )
    ipix = lower >> shift
    return level, ipix


def uniq_to_range(uniq: int) -> Tuple[int, int]:
    """Convert an IVOA UNIQ integer directly to its deepest-level interval.

    Convenience composition of :func:`uniq_to_level_ipix` and
    :func:`level_ipix_to_range`. Useful for iterating over ``mocpy.MOC.uniq_hpx``
    and producing rows for bulk insertion.
    """
    level, ipix = uniq_to_level_ipix(uniq)
    return level_ipix_to_range(level, ipix)


def skycoord_to_point(skycoord) -> int:
    """Map a single sky position to a deepest-level NESTED pixel index.

    Lazy-imports ``cdshealpix`` so this module can be imported (and the
    integer-only helpers tested) without that dependency being installed.

    Args:
        skycoord: An :class:`astropy.coordinates.SkyCoord` instance, scalar.

    Returns:
        The NESTED pixel index at order :data:`LEVEL`, as a Python ``int``.
    """
    from cdshealpix import lonlat_to_healpix  # type: ignore[import-not-found]

    sc = skycoord.icrs
    # cdshealpix >= 0.8 requires Longitude/Latitude (with units) rather
    # than raw float radians; the underlying call accepts the .ra/.dec
    # attributes of a SkyCoord directly.
    ipix = lonlat_to_healpix(sc.ra, sc.dec, depth=LEVEL)
    # cdshealpix returns a numpy scalar/array; coerce to plain int.
    return int(ipix)


def moc_to_ranges(moc) -> Iterator[Tuple[int, int]]:
    """Yield ``(lower, upper)`` deepest-level intervals for each tile of a MOC.

    Iterates the MOC's UNIQ pixels and delegates to :func:`uniq_to_range`.
    Used by :func:`tom_regions.utils.bulk_create_tiles` to materialize a
    :class:`mocpy.MOC` into ``RegionTile`` rows.

    Args:
        moc: A :class:`mocpy.MOC` instance.

    Yields:
        One ``(lower, upper)`` tuple per UNIQ pixel.
    """
    # mocpy >= 0.16 exposes ``uniq_hpx`` returning a numpy array of UNIQ ints.
    for uniq in moc.uniq_hpx:
        yield uniq_to_range(int(uniq))


def iter_uniq_indices(uniqs: Iterable[int]) -> Iterator[Tuple[int, int]]:
    """Convenience: convert an iterable of UNIQ ints to ``(lower, upper)`` tuples.

    Useful when ingesting MOCs from non-mocpy sources (e.g. Aladin's JSON
    payload, which is a dict keyed by depth with lists of ipix at each depth;
    a caller can compute UNIQs from that and feed them here).
    """
    for u in uniqs:
        yield uniq_to_range(int(u))
