"""HEALPix encoding constants for tom_regions.healpix_django.

This module is the canonical source for the numeric constants that govern how
HEALPix tiles are packed into PostgreSQL ``int8range`` values. All other
modules in :mod:`tom_regions.healpix_django` derive their behavior from the
constants defined here.

Background
----------
HEALPix subdivides the sphere into 12 base pixels at ``order = 0`` and then
recursively splits each base pixel into 4 children at every higher order.
At HEALPix order ``k``, there are ``12 * 4**k`` equal-area pixels.

We use the **NESTED** numbering scheme exclusively. NESTED has the property
that every level-``k`` pixel ``ipix`` corresponds to a contiguous range of
level-``L`` pixel indices, namely::

    [ipix << (2*(L-k)),  (ipix+1) << (2*(L-k)) )

That property is what allows us to encode a multi-resolution coverage map
(MOC) -- a heterogeneous set of pixels at varying levels -- as a *set of
disjoint integer intervals at one fixed deepest level*. The deepest level we
support is ``LEVEL = 29``: see :data:`LEVEL` below for why.

Once tiles are intervals, set-theoretic operations (containment, overlap,
union, difference) reduce to interval arithmetic, which PostgreSQL provides
natively as range types and SP-GiST indexes.

References
----------
- Gorski et al. 2005, "HEALPix: A Framework for High-Resolution
  Discretization and Fast Analysis of Data Distributed on the Sphere"
  https://arxiv.org/abs/astro-ph/0409513 (a more accessible primer is
  https://arxiv.org/abs/astro-ph/9905275).
- Singer et al. 2022, "HEALPix Alchemy: fast all-sky spatial analysis using
  the PostgreSQL ORDBMS." https://arxiv.org/abs/2112.06947

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django` (the package overview).
- Next:     :mod:`tom_regions.healpix_django.encoding`, which uses these
            constants to define the conversions between HEALPix
            representations.
"""

from __future__ import annotations

from math import pi

# Deepest HEALPix order we support. ipix at order 29 fits in a signed 64-bit
# integer (12 * 4**29 ~= 3.5e18 < 2**63 - 1 ~= 9.2e18), so a tile interval
# upper bound also fits. A pixel at order 29 subtends roughly 0.4 mas^2 of
# solid angle -- finer than any current astronomical instrument resolves.
# Choosing this as the universal "deepest" level means every coarser tile
# can be expanded into an int8 range without precision loss.
LEVEL: int = 29

# Pixels per side of one of the 12 base faces at the deepest order.
NSIDE: int = 1 << LEVEL  # 2**29

# Total number of pixels at the deepest order.
NPIX: int = 12 * (NSIDE * NSIDE)

# Solid angle subtended by a single deepest-level pixel, in steradians.
# Multiplied by ``upper - lower`` of an int8range, this gives a tile's area.
PIXEL_AREA_STER: float = (4.0 * pi) / NPIX

# Conversion from steradians to square degrees. ``area_sr`` on the
# database side stays in SI units; the user-facing displays (table
# column, detail card, Aladin popup, filter inputs) convert to
# deg^2 because that's what astronomers reach for. The whole sky is
# 4 * pi sr ~= 41,253 deg^2.
SQ_DEG_PER_STERADIAN: float = (180.0 / pi) ** 2


def shift_for_level(level: int) -> int:
    """Return the bit shift between order ``level`` and order :data:`LEVEL`.

    A pixel ``ipix`` at order ``level`` corresponds, in NESTED numbering, to
    the contiguous range of deepest-level pixels::

        [ipix << shift_for_level(level), (ipix + 1) << shift_for_level(level))

    Args:
        level: HEALPix order, ``0 <= level <= LEVEL``.

    Returns:
        Number of bit positions to shift left to convert a level-``level``
        pixel index into a deepest-level lower-bound.

    Raises:
        ValueError: If ``level`` is outside ``[0, LEVEL]``.
    """
    if not 0 <= level <= LEVEL:
        raise ValueError(f"level must be in [0, {LEVEL}], got {level!r}")
    return 2 * (LEVEL - level)
