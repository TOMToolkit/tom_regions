"""Tests for the region-membership selectors in :mod:`tom_regions.services.queries`.

Read these as worked examples of "what falls inside this region?":

- :func:`region_contains_point` -- the single-point primitive.
- :func:`targets_in_region` -- the bounded "score these candidates" selector.

The selectors are model-agnostic (they read ``.ra``/``.dec`` off whatever you
pass), so these tests use lightweight stand-in objects rather than real
``Target`` rows. That keeps the suite hermetic -- the standalone test boot
(:mod:`tom_regions.tests.boot_django`) intentionally does not install
tom_targets. The real-``Target`` wiring is exercised in the backing test TOM.
"""

from __future__ import annotations

from types import SimpleNamespace

from astropy import units as u
from astropy.coordinates import Angle, Latitude, Longitude
from django.test import TestCase
from mocpy import MOC

from tom_regions.base_models import REGION_TYPE_OTHER
from tom_regions.services.queries import region_contains_point, targets_in_region


def _cone_moc(ra_deg: float, dec_deg: float, radius_deg: float, max_depth: int = 8) -> MOC:
    """A cone MOC from plain numbers (mocpy 0.19 wants keyword, unit-bearing args)."""
    return MOC.from_cone(
        lon=Longitude(ra_deg * u.deg),
        lat=Latitude(dec_deg * u.deg),
        radius=Angle(radius_deg * u.deg),
        max_depth=max_depth,
    )


def _target(ra, dec, name):
    """A minimal stand-in for a Target: just the attributes the selectors read."""
    return SimpleNamespace(ra=ra, dec=dec, name=name)


class TargetsInRegionTests(TestCase):
    """A single 1-degree cone on the Crab Nebula; ask which points/targets are inside."""

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region
        from tom_regions.utils import bulk_create_tiles

        cls.crab = Region.objects.create(name="crab cone", type=REGION_TYPE_OTHER)
        bulk_create_tiles(cls.crab, _cone_moc(83.633, 22.0145, 1.0))

    def test_region_contains_point_true_inside_false_outside(self):
        # The cone center is inside; the celestial origin (~80 deg away) is not.
        self.assertTrue(region_contains_point(self.crab, 83.633, 22.0145))
        self.assertFalse(region_contains_point(self.crab, 0.0, 0.0))

    def test_targets_in_region_keeps_only_those_inside(self):
        inside = _target(83.633, 22.0145, "inside")          # at the cone center
        outside = _target(83.633, 25.0145, "outside")        # 3 deg north of a 1 deg cone
        pole = _target(37.95, 89.26, "pole")                 # near the north pole
        result = targets_in_region(self.crab, [inside, outside, pole])
        self.assertEqual([t.name for t in result], ["inside"])

    def test_targets_without_coordinates_are_skipped(self):
        # A non-sidereal target (no ra/dec) has no fixed pixel to test; it is
        # silently dropped rather than raising.
        non_sidereal = _target(None, None, "non_sidereal")
        inside = _target(83.633, 22.0145, "inside")
        result = targets_in_region(self.crab, [non_sidereal, inside])
        self.assertEqual([t.name for t in result], ["inside"])

    def test_input_order_is_preserved(self):
        a = _target(83.7, 22.0, "a")
        b = _target(83.6, 22.1, "b")
        result = targets_in_region(self.crab, [b, a])
        self.assertEqual([t.name for t in result], ["b", "a"])
