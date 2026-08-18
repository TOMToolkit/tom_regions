"""Tests for probability-skymap ingest, credible contours, and point scoring.

These exercise :mod:`tom_regions.services.skymap` against *synthetic* multi-order
skymaps built in-memory, so the assertions have exact, hand-checkable answers and
the suite needs no downloaded FITS fixture.

The fixture trick: a HEALPix cell at order ``level`` subtends a known solid angle
``cell_area``. Setting ``PROBDENSITY = prob / cell_area`` makes that cell's
*integrated* probability exactly ``prob``. So a table built from a list of
per-cell probabilities has a total probability equal to their sum, and its
credible regions fall on cell boundaries we can predict.

What each class demonstrates:

- :class:`SkymapIngestTests` -- ingest writes one density-bearing tile per cell,
  and the stored densities integrate back to 1.
- :class:`CredibleContourTests` -- the X% contour of eight equal cells is the
  smallest whole number of cells reaching X% (0.25 -> 2, 0.5 -> 4, 0.75 -> 6),
  and its reported area matches.
- :class:`CredibleProbabilitiesSettingTests` -- the contour list is the
  overridable ``CREDIBLE_REGION_PROBABILITIES`` setting.
- :class:`ProbabilityAtPointTests` -- a point's enclosed probability is the
  cumulative probability of every cell at least as dense as its own.
"""

from __future__ import annotations

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import Table
from cdshealpix import healpix_to_lonlat
from django.db.models import F, Sum
from django.test import TestCase

from tom_regions.healpix_django.constants import LEVEL, PIXEL_AREA_STER, SQ_DEG_PER_STERADIAN
from tom_regions.healpix_django.encoding import level_ipix_to_uniq
from tom_regions.healpix_django.functions import TileArea
from tom_regions.services.skymap import (
    credible_contours,
    credible_probabilities,
    ingest_skymap,
    probability_at_point,
)


def _cell_area_sr(level: int) -> float:
    """Solid angle (steradians) of one HEALPix cell at ``level``."""
    return PIXEL_AREA_STER * (4 ** (LEVEL - level))


def _skymap_table(level: int, ipix_list: list[int], probs: list[float]) -> Table:
    """Build a synthetic multi-order skymap whose cells carry exact probabilities.

    ``PROBDENSITY = prob / cell_area`` makes each cell's integrated probability
    equal to the given ``prob``. Cells are addressed by ``(level, ipix)`` and
    encoded as UNIQ, the multi-order-map convention.
    """
    cell_area = _cell_area_sr(level)
    uniq = np.array([level_ipix_to_uniq(level, ip) for ip in ipix_list], dtype=np.uint64)
    density = np.array([p / cell_area for p in probs], dtype=float)
    return Table({"UNIQ": uniq, "PROBDENSITY": density})


# Eight equal-probability cells at order 6. The ipix are non-adjacent so mocpy
# does not merge any four siblings into a parent -- each stays a distinct order-6
# cell, which keeps the credible-region cell counts exact.
_UNIFORM_LEVEL = 6
_UNIFORM_IPIX = [0, 1000, 5000, 12000, 20000, 30000, 40000, 48000]


class _UniformSkymapTestCase(TestCase):
    """Base fixture (no test methods): one region of eight equal cells."""

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region

        cls.table = _skymap_table(_UNIFORM_LEVEL, _UNIFORM_IPIX, [0.125] * 8)
        cls.region = Region.objects.create(name="uniform skymap")
        ingest_skymap(cls.region, cls.table)


class SkymapIngestTests(_UniformSkymapTestCase):
    def test_ingest_creates_one_density_bearing_tile_per_cell(self):
        from tom_regions.models import RegionTile

        self.assertEqual(self.region.n_tiles, 8)
        # A skymap region is defined by its tiles carrying a probdensity; none
        # should be NULL here.
        null_density = RegionTile.objects.filter(
            region=self.region, probdensity__isnull=True
        ).count()
        self.assertEqual(null_density, 0)

    def test_stored_densities_integrate_to_one(self):
        from tom_regions.models import RegionTile

        total = RegionTile.objects.filter(region=self.region).aggregate(
            p=Sum(F("probdensity") * TileArea(F("hpx")))
        )["p"]
        self.assertAlmostEqual(total, 1.0, places=6)


class CredibleContourTests(_UniformSkymapTestCase):
    def test_contour_area_matches_expected_cell_count(self):
        cell_area_deg2 = _cell_area_sr(_UNIFORM_LEVEL) * SQ_DEG_PER_STERADIAN
        contours = credible_contours(self.table, [0.25, 0.5, 0.75])
        # Eight equal cells: the X% region is the smallest whole number of cells
        # whose summed probability reaches X% -- here 0.25 -> 2, 0.5 -> 4,
        # 0.75 -> 6 (each cell is 0.125).
        for prob, expected_cells in [(0.25, 2), (0.5, 4), (0.75, 6)]:
            _moc, area_deg2 = contours[prob]
            self.assertAlmostEqual(
                area_deg2,
                expected_cells * cell_area_deg2,
                places=6,
                msg=f"{prob:.0%} contour should cover {expected_cells} of the 8 cells",
            )

    def test_contours_grow_with_credible_level(self):
        contours = credible_contours(self.table, [0.25, 0.5, 0.75])
        areas = [contours[p][1] for p in (0.25, 0.5, 0.75)]
        self.assertEqual(areas, sorted(areas))
        self.assertLess(areas[0], areas[-1])


class CredibleProbabilitiesSettingTests(TestCase):
    """The contour list is the overridable ``CREDIBLE_REGION_PROBABILITIES`` setting."""

    def test_default_is_sorted_descending(self):
        self.assertEqual(credible_probabilities(), [0.95, 0.9, 0.75, 0.5, 0.25])

    def test_list_override(self):
        with self.settings(CREDIBLE_REGION_PROBABILITIES=[0.5, 0.9]):
            self.assertEqual(credible_probabilities(), [0.9, 0.5])

    def test_json_string_override(self):
        # A settings module that read the value straight from an env var leaves
        # it as a JSON string; credible_probabilities parses it.
        with self.settings(CREDIBLE_REGION_PROBABILITIES="[0.3, 0.6]"):
            self.assertEqual(credible_probabilities(), [0.6, 0.3])


class ProbabilityAtPointTests(TestCase):
    """A point's enclosed probability = cumulative probability from the densest cell down."""

    LEVEL_USED = 5
    IPIX = [0, 100, 1000, 5000]
    PROBS = [0.4, 0.3, 0.2, 0.1]  # distinct, descending, summing to 1
    CUMULATIVE = [0.4, 0.7, 0.9, 1.0]

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region

        cls.region = Region.objects.create(name="weighted skymap")
        ingest_skymap(cls.region, _skymap_table(cls.LEVEL_USED, cls.IPIX, cls.PROBS))

    def _cell_center(self, ipix: int) -> SkyCoord:
        lon, lat = healpix_to_lonlat(np.array([ipix], dtype=np.uint64), depth=self.LEVEL_USED)
        return SkyCoord(lon[0], lat[0])

    def test_enclosed_probability_is_cumulative_from_densest(self):
        # The point inside the k-th densest cell sits in the credible region that
        # encloses the top-k cells -- e.g. the 0.3 cell is inside the 70% region.
        for ipix, prob, expected in zip(self.IPIX, self.PROBS, self.CUMULATIVE):
            enclosed = probability_at_point(self.region, self._cell_center(ipix))
            self.assertAlmostEqual(
                enclosed,
                expected,
                places=6,
                msg=f"the {prob} cell should enclose {expected} of the probability",
            )

    def test_point_outside_region_returns_none(self):
        # The center of a cell we never ingested lies in no tile.
        outside = self._cell_center(8000)
        self.assertIsNone(probability_at_point(self.region, outside))
