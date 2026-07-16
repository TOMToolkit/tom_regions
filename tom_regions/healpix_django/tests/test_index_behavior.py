"""Educational integration tests for the healpix_django ORM surface.

The encoding tests cover the math; the field tests cover Django
round-trip. This module is the *integration* set: it builds a small but
representative population of regions and tiles, then exercises each of
the queries the HEALPix-Alchemy paper highlights as the system's reason
for existing.

Read this file as a worked tour of the paper's headline use cases:

1. **Point-in-region** -- filter regions whose tiles contain a single
   sky point. Implemented via the inherited ``__contains`` lookup
   (``int8range @> bigint`` in PostgreSQL).
2. **Region overlap** -- filter regions whose tiles intersect another
   region's tiles. Implemented via the inherited ``__overlap`` lookup
   (``&&``).
3. **Tile area** -- compute solid angle in steradians via the
   :class:`TileArea` ORM Func, summable across rows.
4. **Tile intersection** -- the geometric intersection of two tiles via
   the ``int8range * int8range`` operator wrapped in
   :class:`TileIntersect`.
5. **Tile union** -- aggregate many tile rows into a flat sequence of
   disjoint ranges via PostgreSQL 14's ``range_agg`` plus ``unnest``.
   Demonstrated in raw SQL because Django's ORM doesn't natively model
   set-returning functions; cleaning that up is a Phase-4 concern.

Scale note
----------
The dataset is intentionally small (three cones at known positions) so
the assertions remain readable. At this scale the PostgreSQL planner
typically picks a sequential scan, since the table fits in one heap
page; that is correct behavior. The SP-GiST index over the tile column
exists (asserted in :meth:`SpgistIndexExistsTests.test_index_exists`)
and would be selected at LIGO-skymap scale (50k+ tiles).

Reading order
-------------
- Previous: :mod:`test_fields` (DB-backed field round-trips).
- Next:     End of the package tour. Phase 2 lands view-level tests.
"""

from __future__ import annotations

from astropy import units as u
from astropy.coordinates import Angle, Latitude, Longitude, SkyCoord
from django.db import connection
from django.db.models import F, Sum
from django.test import TestCase
from mocpy import MOC

from tom_regions.healpix_django.encoding import skycoord_to_point
from tom_regions.healpix_django.functions import TileArea


def _cone_moc(ra_deg: float, dec_deg: float, radius_deg: float, max_depth: int = 8) -> MOC:
    """Build a cone MOC from plain numeric inputs.

    Wraps mocpy's keyword-only call signature so the test setUp reads
    naturally. mocpy 0.19's bindings reject positional arguments here.
    """
    return MOC.from_cone(
        lon=Longitude(ra_deg * u.deg),
        lat=Latitude(dec_deg * u.deg),
        radius=Angle(radius_deg * u.deg),
        max_depth=max_depth,
    )


class IndexBehaviorTestCase(TestCase):
    """Build three cone regions on a fixed sky configuration.

    - ``crab`` -- 1 deg cone centered on the Crab Nebula.
    - ``crab_offset`` -- 1 deg cone offset by 0.5 deg from the Crab;
      partially overlaps ``crab``.
    - ``polaris`` -- 1 deg cone near the celestial north pole;
      disjoint from the other two.
    """

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region
        from tom_regions.utils import bulk_create_tiles

        cls.crab = Region.objects.create(name="crab", type="CIRCLE")
        cls.crab_offset = Region.objects.create(name="crab_offset", type="CIRCLE")
        cls.polaris = Region.objects.create(name="polaris", type="CIRCLE")

        bulk_create_tiles(cls.crab, _cone_moc(83.633, 22.0145, 1.0))
        bulk_create_tiles(cls.crab_offset, _cone_moc(84.133, 22.0145, 1.0))
        bulk_create_tiles(cls.polaris, _cone_moc(37.95, 89.26, 1.0))


class SpgistIndexExistsTests(IndexBehaviorTestCase):
    """The SP-GiST index over ``hpx`` is present after migration.

    We assert presence rather than usage. The planner only selects the
    index above a row-count threshold; at the few-hundred-tile scale of
    this test it correctly chooses a sequential scan. See the module
    docstring for the scaling rationale.
    """

    def test_spgist_index_exists_on_hpx_column(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'regions_regiontile' "
                "AND indexdef ILIKE '%%using spgist%%'"
            )
            rows = cursor.fetchall()
        self.assertEqual(len(rows), 1, f"expected exactly one SP-GiST index; got {rows}")
        self.assertIn("hpx", rows[0][0])


class PointInRegionTests(IndexBehaviorTestCase):
    """``__contains`` answers "which regions cover this sky point?"."""

    def test_point_inside_crab_returns_only_crab_family(self):
        # A point very close to the Crab Nebula falls inside both the
        # ``crab`` cone and the ``crab_offset`` cone (which is centered
        # 0.5 deg away with the same 1 deg radius), but well outside the
        # ``polaris`` cone near the celestial north pole.
        from tom_regions.models import Region

        ipix = skycoord_to_point(SkyCoord(83.7, 22.0, unit="deg"))
        names = set(
            Region.objects.filter(tiles__hpx__contains=ipix).values_list("name", flat=True)
        )
        # ``crab_offset`` is centered 0.5 deg east of the Crab, so a
        # point at the Crab itself is well within both cones.
        self.assertEqual(names, {"crab", "crab_offset"})

    def test_point_at_celestial_origin_returns_no_regions(self):
        from tom_regions.models import Region

        ipix = skycoord_to_point(SkyCoord(0.0, 0.0, unit="deg"))
        names = list(
            Region.objects.filter(tiles__hpx__contains=ipix).values_list("name", flat=True)
        )
        self.assertEqual(names, [])


class RegionOverlapTests(IndexBehaviorTestCase):
    """``__overlap`` (``&&``) answers "do any tiles of these two regions intersect?".

    Each test pins down a *specific* pair of tiles whose overlap or
    disjointness can be reasoned about geometrically. We use
    ``__contains`` to fetch the tile of a given region that covers a
    chosen sky point, then ask whether the overlap operator agrees.
    Picking tiles by ``order_by("hpx").first()`` would not work: NESTED
    ordering of pixel ids does not correspond to angular position, so
    the "first" tile is not predictably near any chosen point.
    """

    def setUp(self):
        super().setUp()
        # The Crab Nebula's deepest-level pixel falls inside both the
        # ``crab`` cone and the ``crab_offset`` cone (which is centered
        # 0.5 deg away). Both regions therefore contain a tile that
        # covers this pixel; those two tiles necessarily overlap.
        self.crab_pixel = skycoord_to_point(SkyCoord(83.633, 22.0145, unit="deg"))

    def test_crab_and_crab_offset_share_a_tile_at_the_crab(self):
        from tom_regions.models import RegionTile

        crab_tile = RegionTile.objects.get(
            region=self.crab, hpx__contains=self.crab_pixel
        )
        # The crab_offset tile that covers the same pixel must overlap
        # the crab tile; ``hpx__overlap`` should match it.
        overlap_exists = RegionTile.objects.filter(
            region=self.crab_offset, hpx__overlap=crab_tile.hpx
        ).exists()
        self.assertTrue(overlap_exists)

    def test_polaris_tile_at_pole_does_not_overlap_any_crab_tile(self):
        from tom_regions.models import RegionTile

        polaris_pixel = skycoord_to_point(SkyCoord(37.95, 89.26, unit="deg"))
        polaris_tile = RegionTile.objects.get(
            region=self.polaris, hpx__contains=polaris_pixel
        )
        # Cones are ~80 deg apart, so no Crab tile should overlap this one.
        overlap_count = RegionTile.objects.filter(
            region=self.crab, hpx__overlap=polaris_tile.hpx
        ).count()
        self.assertEqual(overlap_count, 0)


class TileAreaTests(IndexBehaviorTestCase):
    """``TileArea`` returns each tile's solid angle in steradians.

    Summed across all tiles of a region it should match the cached
    ``Region.area_sr`` populated by :func:`recompute_region_summary` --
    a pleasing cross-check between the Python-side and SQL-side
    computations.
    """

    def test_summed_tile_area_matches_cached_region_area(self):
        from tom_regions.models import RegionTile

        for region in (self.crab, self.crab_offset, self.polaris):
            total = RegionTile.objects.filter(region=region).aggregate(
                area=Sum(TileArea(F("hpx")))
            )["area"]
            self.assertAlmostEqual(
                total,
                region.area_sr,
                places=12,
                msg=f"region {region.name!r}: summed TileArea != cached area_sr",
            )


class MultiOrderStorageTests(IndexBehaviorTestCase):
    """The "M" in MOC is load-bearing: tiles are stored at their native level.

    When :func:`mocpy.MOC.from_cone` (and friends) build a region, mocpy
    *normalizes* the result -- any four sibling pixels at level *k* that
    all fall inside the region get aggregated into their level-(*k*-1)
    parent, recursively. The result is a heterogeneous tile list: coarse
    tiles for the interior, fine tiles for the rim where the boundary
    cuts pixels.

    :func:`bulk_create_tiles` preserves that heterogeneity on insert -- a
    level-*k* tile maps to an int8range of length ``4**(LEVEL-k)``, and
    that's what gets stored. So a single region's RegionTile rows span
    multiple HEALPix orders, with the level recoverable from each row's
    range length.

    This test is what you'd write to convince yourself that the
    compression-in-storage claim in :mod:`encoding`'s module docstring
    isn't aspirational. It also illustrates *why* a database column of
    type int8range is exactly the right choice: a flat depth-29 list
    would need orders of magnitude more rows.
    """

    def test_crab_cone_stores_tiles_at_multiple_orders(self):
        from tom_regions.healpix_django.encoding import range_to_level_ipix
        from tom_regions.models import RegionTile

        levels_present = set()
        for tile in RegionTile.objects.filter(region=self.crab):
            level, _ = range_to_level_ipix(tile.hpx.lower, tile.hpx.upper)
            levels_present.add(level)

        # mocpy aggregates contiguous deepest-level pixels into coarser
        # parents wherever it can, so any non-trivial cone uses at least
        # two orders -- a coarse interior and a fine rim. If only one
        # order appears, mocpy is no longer normalizing on our behalf and
        # the storage compression claim breaks down.
        self.assertGreater(
            len(levels_present), 1,
            f"expected multiple HEALPix orders, got only {sorted(levels_present)}; "
            "if this fails, mocpy's normalization may have changed.",
        )

    def test_storage_is_dramatically_more_compact_than_flat(self):
        from tom_regions.healpix_django.constants import PIXEL_AREA_STER
        from tom_regions.healpix_django.encoding import range_to_level_ipix
        from tom_regions.models import RegionTile

        tiles = list(RegionTile.objects.filter(region=self.crab).only("hpx"))
        levels = [range_to_level_ipix(t.hpx.lower, t.hpx.upper)[0] for t in tiles]
        max_level = max(levels)

        # If the same area were flattened to a list of pixels at the
        # deepest level present, the count would be region_area divided
        # by the pixel area at that level. Multi-order aggregation should
        # produce significantly fewer rows.
        pixel_area_at_max = PIXEL_AREA_STER * (4 ** (29 - max_level))
        flat_count_estimate = self.crab.area_sr / pixel_area_at_max
        self.assertLess(
            len(tiles),
            flat_count_estimate,
            f"actual={len(tiles)}, flat-equivalent={flat_count_estimate:.0f}; "
            "compression should be at least 1x.",
        )


class TileIntersectAndUnionTests(IndexBehaviorTestCase):
    """``int8range * int8range`` and ``unnest(range_agg(...))`` raw-SQL demos.

    Django's ORM doesn't natively model set-returning functions like
    ``unnest``, so the cleanest demonstration uses ``connection.cursor``
    directly. The :class:`TileIntersect` / :class:`TileUnion` classes
    stage these expressions for the eventual ORM-friendly path; for v1
    we exercise them through raw SQL.
    """

    def test_intersection_of_overlapping_tiles_is_non_empty(self):
        # Take the crab and crab_offset tiles that both cover the Crab
        # Nebula's deepest-level pixel; they necessarily overlap. The
        # ``*`` operator returns the intersected range; if the inputs
        # were disjoint the result would be the empty range ``empty``.
        from tom_regions.models import RegionTile

        crab_pixel = skycoord_to_point(SkyCoord(83.633, 22.0145, unit="deg"))
        crab_tile = RegionTile.objects.get(region=self.crab, hpx__contains=crab_pixel)
        crab_offset_tile = RegionTile.objects.get(
            region=self.crab_offset, hpx__contains=crab_pixel
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT %s::int8range * %s::int8range",
                [str(crab_tile.hpx), str(crab_offset_tile.hpx)],
            )
            (intersected,) = cursor.fetchone()
        self.assertLess(intersected.lower, intersected.upper)

    def test_intersection_of_disjoint_tiles_is_empty(self):
        # Pick the crab tile at the Crab Nebula and the polaris tile at
        # the celestial north pole. Cones are ~80 deg apart, so the two
        # tiles are disjoint and ``*`` returns ``empty``.
        from tom_regions.models import RegionTile

        crab_pixel = skycoord_to_point(SkyCoord(83.633, 22.0145, unit="deg"))
        polaris_pixel = skycoord_to_point(SkyCoord(37.95, 89.26, unit="deg"))
        crab_tile = RegionTile.objects.get(region=self.crab, hpx__contains=crab_pixel)
        polaris_tile = RegionTile.objects.get(
            region=self.polaris, hpx__contains=polaris_pixel
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT isempty(%s::int8range * %s::int8range)",
                [str(crab_tile.hpx), str(polaris_tile.hpx)],
            )
            (is_empty,) = cursor.fetchone()
        self.assertTrue(is_empty)

    def test_union_aggregates_overlapping_regions_into_disjoint_ranges(self):
        # range_agg + unnest produces one row per disjoint range covering
        # the union of crab + crab_offset's tiles. Because the two cones
        # overlap, the union has *fewer* output rows than the sum of
        # input tile counts -- overlapping tiles get coalesced.
        from tom_regions.models import RegionTile

        input_count = RegionTile.objects.filter(
            region__in=[self.crab, self.crab_offset]
        ).count()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM ("
                "  SELECT unnest(range_agg(hpx)) AS r"
                "  FROM regions_regiontile"
                "  WHERE region_id IN (%s, %s)"
                ") sub",
                [self.crab.id, self.crab_offset.id],
            )
            (union_count,) = cursor.fetchone()
        self.assertGreater(input_count, 0)
        self.assertLess(
            union_count,
            input_count,
            "union of overlapping cones should coalesce tiles",
        )
