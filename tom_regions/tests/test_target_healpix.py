"""Tests for the TargetHealpix side table and the indexed region-membership join.

Because ``TargetHealpix`` stores a plain ``target_id`` (not a foreign key to
tom_targets), the indexed join can be exercised hermetically: we insert cache
rows directly and assert which ones the region's tiles enclose. The signal that
*populates* TargetHealpix from real Target saves, and the tom_targets-resolving
default of :func:`targets_in_region`, are verified in the backing test TOM where
tom_targets is installed.

What these demonstrate:

- :func:`target_ids_in_region` -- the single indexed join
  (``regiontile.hpx @> targethealpix.hpx``) that scales region membership to the
  whole target table.
- :func:`regions_containing_target` -- the reverse query that powers the target
  detail page's "Regions" tab.
"""

from __future__ import annotations

from types import SimpleNamespace

from astropy import units as u
from astropy.coordinates import Angle, Latitude, Longitude, SkyCoord
from django.test import TestCase
from mocpy import MOC

from tom_regions.base_models import REGION_TYPE_OTHER
from tom_regions.healpix_django.encoding import skycoord_to_point
from tom_regions.services.queries import regions_containing_target, target_ids_in_region


def _cone_moc(ra_deg: float, dec_deg: float, radius_deg: float, max_depth: int = 8) -> MOC:
    return MOC.from_cone(
        lon=Longitude(ra_deg * u.deg),
        lat=Latitude(dec_deg * u.deg),
        radius=Angle(radius_deg * u.deg),
        max_depth=max_depth,
    )


class IndexedMembershipTests(TestCase):
    """A 1-degree cone on the Crab Nebula, with cache rows inside and outside it."""

    @classmethod
    def setUpTestData(cls):
        from tom_regions.models import Region, TargetHealpix
        from tom_regions.utils import bulk_create_tiles

        cls.crab = Region.objects.create(name="crab", type=REGION_TYPE_OTHER)
        bulk_create_tiles(cls.crab, _cone_moc(83.633, 22.0145, 1.0))

        # Two points inside the cone, one well outside it (3 deg north).
        TargetHealpix.objects.create(
            target_id=11, hpx=skycoord_to_point(SkyCoord(83.633, 22.0145, unit="deg"))
        )
        TargetHealpix.objects.create(
            target_id=12, hpx=skycoord_to_point(SkyCoord(83.7, 22.0, unit="deg"))
        )
        TargetHealpix.objects.create(
            target_id=99, hpx=skycoord_to_point(SkyCoord(83.633, 25.0145, unit="deg"))
        )

    def test_target_ids_in_region_returns_only_inside(self):
        self.assertEqual(set(target_ids_in_region(self.crab)), {11, 12})

    def test_regions_containing_target_is_the_reverse(self):
        inside = SimpleNamespace(ra=83.633, dec=22.0145)
        names = set(regions_containing_target(inside).values_list("name", flat=True))
        self.assertEqual(names, {"crab"})

        outside = SimpleNamespace(ra=0.0, dec=0.0)
        self.assertEqual(list(regions_containing_target(outside)), [])

    def test_regions_containing_target_without_coordinates_is_empty(self):
        no_coords = SimpleNamespace(ra=None, dec=None)
        self.assertEqual(list(regions_containing_target(no_coords)), [])
