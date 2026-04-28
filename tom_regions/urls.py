"""URL routes for tom_regions.

Phase 1 ships no routes; the module exists so
``apps.TomRegionsConfig.include_url_paths()`` can register the namespace
ahead of Phase 2, which adds ``list``, ``create``, ``<pk>/``, ``<pk>/update/``,
``<pk>/delete/``, ``<pk>/moc.json``, and ``<pk>/targets/``.
"""

from __future__ import annotations

app_name = "tom_regions"

urlpatterns: list = []
