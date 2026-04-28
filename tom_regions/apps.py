"""AppConfig for tom_regions.

Two responsibilities beyond the boilerplate:

1. ``ready()`` calls :func:`tom_regions.healpix_django.assert_postgres_supported`
   so non-PostgreSQL backends fail loudly at app boot rather than at the
   first query.
2. The four TOMToolkit plugin hooks (``target_detail_buttons``,
   ``nav_items``, ``include_url_paths``, ``profile_details``) are the
   integration points the host TOM calls. In Phase 1 these return safe
   no-ops; Phase 2 fills them in once the list/detail views, navbar
   partial, and Aladin overlays are built.
"""

from __future__ import annotations

from django.apps import AppConfig
from django.urls import include, path


class TomRegionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tom_regions"
    label = "regions"

    def ready(self) -> None:  # noqa: D401 - imperative, Django convention
        # Defer the import: ``ready()`` runs after Django has finished
        # loading the app registry, but ``healpix_django.checks`` touches
        # the database connection wrapper, which we don't want imported
        # at module-load time.
        from tom_regions.healpix_django import assert_postgres_supported

        assert_postgres_supported()

    # ------------------------------------------------------------------
    # TOMToolkit plugin hooks. Phase 1 ships intentional no-ops; Phase 2
    # rewrites these to point at the real Region list/detail views, the
    # navbar partial, and the Aladin overlay.
    # ------------------------------------------------------------------

    def target_detail_buttons(self):
        # No button until the regions list/detail views exist (Phase 2).
        return None

    def nav_items(self):
        # No navbar entry until the regions partial exists (Phase 2).
        return []

    def include_url_paths(self):
        # The URL include is wired up now even though urls.py exposes no
        # routes yet. That way Phase 2 can land routes by editing one file.
        return [path("regions/", include("tom_regions.urls", namespace="regions"))]

    def profile_details(self):
        # tom_regions intentionally does not own a per-user profile section.
        return []
