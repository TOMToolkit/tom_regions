"""URL routes for tom_regions.

The four current routes:

- ``regions:list`` -- HTMX-driven list view at ``/regions/``.
- ``regions:create`` -- create form at ``/regions/create/``; mode
  selected by the ``?type=`` query parameter (polygon / circle / moc).
- ``regions:detail`` -- per-region landing page at ``/regions/<pk>/``.
- ``regions:moc-json`` -- IVOA-MOC-JSON endpoint Aladin Lite fetches
  via :js:func:`A.MOCFromJSON`. Lives at ``/regions/<pk>/moc.json``
  so the URL itself reads as a content-type hint.

The URL include is wired from
:meth:`tom_regions.apps.TomRegionsConfig.include_url_paths`, which
mounts this module under the ``regions`` namespace at ``/regions/``.
Update / delete routes will land alongside their views when they're
needed.
"""

from __future__ import annotations

from django.urls import path

from tom_regions.views import (
    RegionAddRemoveGroupingView,
    RegionCreateView,
    RegionDeleteView,
    RegionDetailView,
    RegionGroupingCreateView,
    RegionGroupingDeleteView,
    RegionGroupingView,
    RegionListView,
    RegionMOCJsonView,
    RegionSaveFromAladinView,
)

app_name = "tom_regions"

urlpatterns = [
    path("", RegionListView.as_view(), name="list"),
    # Create page is now FITS-upload-only; the interactive Aladin save
    # flow lives on the list page itself.
    path("create/", RegionCreateView.as_view(), name="create"),
    # Endpoint for the list page's "Save MOC" form section.
    path(
        "save-aladin-moc/",
        RegionSaveFromAladinView.as_view(),
        name="save-aladin-moc",
    ),
    # Region grouping (named collections of regions) -- mirrors the
    # tom_targets ``targetgrouping`` URLs.
    path(
        "grouping/",
        RegionGroupingView.as_view(),
        name="grouping",
    ),
    path(
        "grouping/create/",
        RegionGroupingCreateView.as_view(),
        name="create-group",
    ),
    path(
        "grouping/<int:pk>/delete/",
        RegionGroupingDeleteView.as_view(),
        name="delete-group",
    ),
    path(
        "add-remove-grouping/",
        RegionAddRemoveGroupingView.as_view(),
        name="add-remove-grouping",
    ),
    path("<int:pk>/", RegionDetailView.as_view(), name="detail"),
    path("<int:pk>/delete/", RegionDeleteView.as_view(), name="delete"),
    path("<int:pk>/moc.json", RegionMOCJsonView.as_view(), name="moc-json"),
]
