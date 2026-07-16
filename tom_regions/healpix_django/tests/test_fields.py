"""DB-backed tests for HealpixPointField and HealpixTileField.

Round-trips values through actual PostgreSQL to verify the field
classes work end-to-end. The encoding-only tests in :mod:`test_encoding`
cover the math; this module covers the Django ORM surface: how each
field's ``get_prep_value`` translates Python inputs into SQL, and how
the values come back out as ``NumericRange``/``int`` after a real
database round-trip.

Why test ``get_prep_value`` separately from the round-trip
-----------------------------------------------------------
``get_prep_value`` is the only branchy path in either field; the rest
of the parent classes (``BigIntegerField``, ``BigIntegerRangeField``)
are well-trusted Django code. Splitting the prep-value tests off from
the round-trip tests means the failure messages localize quickly: a
prep-value test failure points at ``fields.py``, while a round-trip
failure points at the database adapter or the migration.

Reading order
-------------
- Previous: :mod:`test_encoding` (UNIQ <-> range round-trips).
- Next:     :mod:`test_index_behavior`, which exercises queries against
            a population of regions and tiles.
"""

from __future__ import annotations

from astropy.coordinates import SkyCoord
from django.test import TestCase
from psycopg2.extras import NumericRange

from tom_regions.healpix_django.encoding import (
    level_ipix_to_range,
    level_ipix_to_uniq,
    skycoord_to_point,
)
from tom_regions.healpix_django.fields import HealpixPointField, HealpixTileField


class HealpixPointFieldPrepValueTests(TestCase):
    """``HealpixPointField.get_prep_value`` accepts int, SkyCoord, or (ra, dec)."""

    def setUp(self):
        self.field = HealpixPointField()

    def test_int_passes_through(self):
        # Integers are treated as already-encoded deepest-level pixel ids;
        # no conversion takes place. This is the path the database adapter
        # uses on the way back out of a query.
        self.assertEqual(self.field.get_prep_value(1234567890), 1234567890)

    def test_none_passes_through(self):
        self.assertIsNone(self.field.get_prep_value(None))

    def test_skycoord_is_encoded_to_deepest_level_index(self):
        # A SkyCoord is converted via ``encoding.skycoord_to_point``; we
        # cross-check by calling that helper directly so a regression
        # in either side fails this test.
        sc = SkyCoord(83.633, 22.0145, unit="deg")
        ipix = self.field.get_prep_value(sc)
        self.assertEqual(ipix, skycoord_to_point(sc))
        self.assertIsInstance(ipix, int)

    def test_ra_dec_tuple_is_encoded_like_skycoord(self):
        ra_dec = (83.633, 22.0145)
        ipix = self.field.get_prep_value(ra_dec)
        self.assertEqual(ipix, skycoord_to_point(SkyCoord(*ra_dec, unit="deg")))

    def test_unsupported_type_raises_typeerror(self):
        with self.assertRaises(TypeError):
            self.field.get_prep_value("not a coordinate")


class HealpixTileFieldPrepValueTests(TestCase):
    """``HealpixTileField.get_prep_value`` accepts NumericRange, UNIQ int, or (lower, upper)."""

    def setUp(self):
        self.field = HealpixTileField()

    def test_none_passes_through(self):
        self.assertIsNone(self.field.get_prep_value(None))

    def test_numeric_range_passes_through(self):
        nr = NumericRange(0, 16, "[)")
        result = self.field.get_prep_value(nr)
        # NumericRange equality uses lower/upper/bounds, so direct == works.
        self.assertEqual(result, nr)

    def test_lower_upper_tuple_is_wrapped_half_open(self):
        result = self.field.get_prep_value((0, 16))
        self.assertEqual((result.lower, result.upper), (0, 16))
        # Half-open: lower inclusive, upper exclusive. Encoded as "[)" in
        # psycopg2's NumericRange bounds string.
        self.assertEqual(result._bounds, "[)")

    def test_uniq_int_is_decoded_to_deepest_level_range(self):
        # UNIQ for (level=0, ipix=0) is the integer 4. The deepest-level
        # range that pixel covers is [0, 4**29).
        uniq_for_level0_ipix0 = level_ipix_to_uniq(0, 0)
        result = self.field.get_prep_value(uniq_for_level0_ipix0)
        expected = level_ipix_to_range(0, 0)
        self.assertEqual((result.lower, result.upper), expected)

    def test_inverted_tuple_raises_valueerror(self):
        with self.assertRaises(ValueError):
            self.field.get_prep_value((100, 50))

    def test_unsupported_type_raises_typeerror(self):
        with self.assertRaises(TypeError):
            self.field.get_prep_value("not a tile")

    def test_bool_is_explicitly_rejected(self):
        # ``isinstance(True, int)`` is True in Python, but a bool is never
        # a meaningful UNIQ index. The field rejects it explicitly so a
        # caller can't accidentally store ``True`` as a tile.
        with self.assertRaises(TypeError):
            self.field.get_prep_value(True)


class RegionTileRoundTripTests(TestCase):
    """End-to-end: insert RegionTile rows with each accepted hpx form, read back.

    These tests exercise the full pipeline: ``get_prep_value`` ->
    PostgreSQL ``int8range`` column -> psycopg2 NumericRange -> Django
    model instance attribute. A regression anywhere in that chain fails
    here.
    """

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region

        cls.region = Region.objects.create(name="round-trip", type="POLYGON")

    def test_numeric_range_input_round_trips(self):
        from tom_regions.models import RegionTile

        nr = NumericRange(100, 200, "[)")
        tile = RegionTile.objects.create(region=self.region, hpx=nr)
        tile.refresh_from_db()
        self.assertEqual((tile.hpx.lower, tile.hpx.upper), (100, 200))

    def test_lower_upper_tuple_input_round_trips(self):
        from tom_regions.models import RegionTile

        tile = RegionTile.objects.create(region=self.region, hpx=(300, 400))
        tile.refresh_from_db()
        self.assertEqual((tile.hpx.lower, tile.hpx.upper), (300, 400))

    def test_uniq_input_round_trips_to_decoded_range(self):
        # A deepest-level pixel UNIQ decodes to a unit interval. We use a
        # different (level, ipix) here than the prep-value test to make
        # sure the inserted row genuinely represents that interval.
        from tom_regions.healpix_django.encoding import level_ipix_to_uniq
        from tom_regions.models import RegionTile

        uniq = level_ipix_to_uniq(29, 12345)
        expected = level_ipix_to_range(29, 12345)
        tile = RegionTile.objects.create(region=self.region, hpx=uniq)
        tile.refresh_from_db()
        self.assertEqual((tile.hpx.lower, tile.hpx.upper), expected)

    def test_probdensity_default_is_null(self):
        # SKYMAP-type regions populate probdensity per tile; ordinary
        # regions leave it null. The column must accept NULL on insert.
        from tom_regions.models import RegionTile

        tile = RegionTile.objects.create(region=self.region, hpx=(500, 600))
        tile.refresh_from_db()
        self.assertIsNone(tile.probdensity)
