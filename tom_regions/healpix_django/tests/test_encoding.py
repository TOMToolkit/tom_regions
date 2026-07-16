"""Pure-Python tests for HEALPix encoding helpers.

These tests do double duty: they verify the encoding math, and they read
linearly as a worked example of how a multi-order coverage map is laid
flat on a single integer axis. A reader who walks through the assertions
might gain insight into the method given by Leo's HEALPix-Alchemy paper.

No Django, no PostgreSQL, no third-party dependencies. ``python -m
unittest`` is enough.
"""

from __future__ import annotations

import unittest

from tom_regions.healpix_django.constants import (
    LEVEL,
    NPIX,
    NSIDE,
    PIXEL_AREA_STER,
    shift_for_level,
)
from tom_regions.healpix_django.encoding import (
    iter_uniq_indices,
    level_ipix_to_range,
    level_ipix_to_uniq,
    range_to_level_ipix,
    uniq_to_level_ipix,
    uniq_to_range,
)


class ConstantsTests(unittest.TestCase):
    """Sanity-checks on the geometry constants the rest of the system trusts.

    The deepest-level pixel count NPIX must fit comfortably inside int63 so
    that interval upper bounds (which can be NPIX itself, for the all-sky
    tile at level 0, ipix 0) survive PostgreSQL's signed bigint column.
    """

    def test_nside_is_power_of_two(self):
        self.assertEqual(NSIDE, 1 << LEVEL)

    def test_npix_is_12_nside_squared(self):
        self.assertEqual(NPIX, 12 * NSIDE * NSIDE)

    def test_npix_fits_in_signed_bigint(self):
        # bigint == signed int64 == [-2**63, 2**63 - 1]. NPIX is the open
        # upper bound of the all-sky range, so we need NPIX <= 2**63 - 1.
        self.assertLess(NPIX, 1 << 63)

    def test_pixel_area_sums_to_4pi(self):
        from math import pi

        self.assertAlmostEqual(NPIX * PIXEL_AREA_STER, 4.0 * pi, places=12)

    def test_shift_for_level_endpoints(self):
        # At the deepest level, no shift is needed: each pixel is its own range.
        self.assertEqual(shift_for_level(LEVEL), 0)
        # At order 0 (the 12 base faces), each pixel covers 4**LEVEL deepest
        # pixels, so the shift is 2*LEVEL bits.
        self.assertEqual(shift_for_level(0), 2 * LEVEL)


class UniqRoundTripTests(unittest.TestCase):
    """Round-trip the IVOA UNIQ encoding against ``(level, ipix)``.

    UNIQ packs a HEALPix order and the NESTED pixel index of that order into a
    single positive integer: ``uniq = 4 * 4**level + ipix``. The encoding is
    self-delimiting -- given just ``uniq``, you can recover both ``level`` and
    ``ipix`` -- which is what makes it convenient for transmitting MOCs.
    """

    def test_smallest_uniq_is_level_0_ipix_0(self):
        # 4 * 4**0 + 0 == 4. Twelve base pixels span uniq 4..15.
        self.assertEqual(uniq_to_level_ipix(4), (0, 0))
        self.assertEqual(uniq_to_level_ipix(15), (0, 11))
        self.assertEqual(level_ipix_to_uniq(0, 0), 4)
        self.assertEqual(level_ipix_to_uniq(0, 11), 15)

    def test_first_level_1_pixel_is_uniq_16(self):
        # The level-0 pixels occupy uniq 4..15. Level-1 starts at 4*4 = 16.
        self.assertEqual(uniq_to_level_ipix(16), (1, 0))
        self.assertEqual(level_ipix_to_uniq(1, 0), 16)

    def test_round_trip_at_each_level(self):
        # For every supported level, taking ipix=0 and ipix=N-1 (last pixel)
        # round-trips through UNIQ without loss.
        for level in range(LEVEL + 1):
            npix = 12 << (2 * level)
            for ipix in (0, 1, npix - 1):
                uniq = level_ipix_to_uniq(level, ipix)
                self.assertEqual(uniq_to_level_ipix(uniq), (level, ipix))

    def test_invalid_uniq_rejected(self):
        with self.assertRaises(ValueError):
            uniq_to_level_ipix(0)
        with self.assertRaises(ValueError):
            uniq_to_level_ipix(3)  # below the smallest legal value (4)


class IntervalRoundTripTests(unittest.TestCase):
    """Demonstrate how NESTED pixels become contiguous deepest-level ranges.

    This is the load-bearing insight of the whole system. A pixel at order
    ``k`` is the union of ``4**(LEVEL - k)`` pixels at the deepest order, and
    in the NESTED scheme those deepest pixels form a single contiguous block
    starting at ``ipix << (2*(LEVEL-k))``. Therefore *any* NESTED tile at any
    order can be represented losslessly by a half-open ``int8range``.

    A direct consequence: the int8range column accepts tiles at *any*
    level (the level is recovered by the range's length), so a single
    multi-order MOC's tiles all live in one homogeneous database column.
    No sidecar level field is needed.

    Once tiles live as integer intervals, set-theoretic queries on coverage
    maps reduce to interval arithmetic, which PostgreSQL implements natively
    and indexes with SP-GiST.
    """

    def test_deepest_level_pixel_is_unit_interval(self):
        # At the deepest level the shift is zero, so a pixel is just [ipix, ipix+1).
        self.assertEqual(level_ipix_to_range(LEVEL, 0), (0, 1))
        self.assertEqual(level_ipix_to_range(LEVEL, 1234), (1234, 1235))

    def test_level_zero_pixels_partition_the_sphere(self):
        # The 12 base pixels at order 0 cover the full deepest-level axis [0, NPIX)
        # without gaps and without overlap. Walk them in order and assert
        # contiguity -- this is essentially the all-sky property of HEALPix in
        # one assertion.
        block = NPIX // 12
        previous_upper = 0
        for ipix in range(12):
            lower, upper = level_ipix_to_range(0, ipix)
            self.assertEqual(lower, previous_upper)
            self.assertEqual(upper - lower, block)
            previous_upper = upper
        self.assertEqual(previous_upper, NPIX)

    def test_a_pixel_contains_its_four_children(self):
        # Sub-divide pixel (level=5, ipix=42) into its four children at level 6.
        # The union of the children's intervals must equal the parent's interval.
        parent_lower, parent_upper = level_ipix_to_range(5, 42)
        child_intervals = [
            level_ipix_to_range(6, 4 * 42 + child)
            for child in range(4)
        ]
        # Children are contiguous and sorted.
        self.assertEqual(child_intervals[0][0], parent_lower)
        for left, right in zip(child_intervals, child_intervals[1:]):
            self.assertEqual(left[1], right[0])
        self.assertEqual(child_intervals[-1][1], parent_upper)

    def test_round_trip_via_range(self):
        # Pick a handful of (level, ipix) at varying orders; round-trip through
        # the interval representation and back.
        cases = [
            (0, 0),
            (0, 11),
            (1, 0),
            (1, 47),
            (5, 1234),
            (LEVEL - 1, 7),
            (LEVEL, 0),
            (LEVEL, NPIX - 1),
        ]
        for level, ipix in cases:
            lower, upper = level_ipix_to_range(level, ipix)
            self.assertEqual(range_to_level_ipix(lower, upper), (level, ipix))

    def test_uniq_to_range_short_circuit(self):
        # uniq_to_range is just a composition; verify it agrees with the long form.
        for uniq in (4, 16, 64, 4 * (4 ** 10) + 7):
            level, ipix = uniq_to_level_ipix(uniq)
            self.assertEqual(uniq_to_range(uniq), level_ipix_to_range(level, ipix))

    def test_misaligned_interval_rejected(self):
        # An interval starting at 1 with length 4 is *not* a NESTED pixel block:
        # NESTED pixels of length 4 must start at multiples of 4.
        with self.assertRaises(ValueError):
            range_to_level_ipix(1, 5)

    def test_non_power_of_four_length_rejected(self):
        # NESTED block lengths are always 4**k. Length 8 (= 2**3) is a power of
        # 2 but not a power of 4, so it can never represent a single NESTED pixel.
        with self.assertRaises(ValueError):
            range_to_level_ipix(0, 8)


class IterUniqIndicesTests(unittest.TestCase):
    """Verify the bulk-iteration helper used by the tile loader."""

    def test_yields_intervals_in_input_order(self):
        # A toy MOC consisting of three pixels at varying levels.
        uniqs = [
            level_ipix_to_uniq(0, 5),
            level_ipix_to_uniq(3, 17),
            level_ipix_to_uniq(LEVEL, 1024),
        ]
        produced = list(iter_uniq_indices(uniqs))
        expected = [
            level_ipix_to_range(0, 5),
            level_ipix_to_range(3, 17),
            level_ipix_to_range(LEVEL, 1024),
        ]
        self.assertEqual(produced, expected)


if __name__ == "__main__":
    unittest.main()
