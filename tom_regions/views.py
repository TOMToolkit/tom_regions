"""Views for tom_regions.

Phase 1 ships no views; the file exists so :mod:`tom_regions.urls` has a
place to import from when Phase 2 lands. Phase 2 will add
``RegionListView``, ``RegionDetailView``, ``RegionCreateView``,
``RegionUpdateView``, ``RegionDeleteView``, and the MOC-JSON endpoint
that the Aladin overlay fetches.
"""

from __future__ import annotations
