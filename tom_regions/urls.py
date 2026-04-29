"""URL routes for tom_regions.

Three Phase 2 routes:

- ``regions:list`` -- the HTMX-driven list view at ``/regions/``.
- ``regions:detail`` -- per-region landing page at ``/regions/<pk>/``.
- ``regions:moc-json`` -- the IVOA-MOC-JSON endpoint Aladin Lite
  fetches via :js:func:`aladin.addMOCFromURL`. Lives at
  ``/regions/<pk>/moc.json`` so the URL itself reads as a content-type
  hint.

The URL include is wired from
:meth:`tom_regions.apps.TomRegionsConfig.include_url_paths`, which
mounts this module under the ``regions`` namespace at
``/regions/``. Phase 3 adds the create / update / delete routes; this
module is the place to add them when the views land.
"""

from __future__ import annotations

from django.urls import path

from tom_regions.views import RegionDetailView, RegionListView, RegionMOCJsonView

app_name = "tom_regions"

urlpatterns = [
    path("", RegionListView.as_view(), name="list"),
    path("<int:pk>/", RegionDetailView.as_view(), name="detail"),
    path("<int:pk>/moc.json", RegionMOCJsonView.as_view(), name="moc-json"),
]
