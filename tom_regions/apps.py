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
        self._connect_target_healpix_signals()

    def _connect_target_healpix_signals(self) -> None:
        """Wire Target -> TargetHealpix cache maintenance, if tom_targets is present.

        tom_regions keeps a deepest-level HEALPix point per target in a side
        table so region-membership queries are a single indexed join. The cache
        is maintained by ``post_save`` / ``post_delete`` on the Target model. We
        connect the receivers here -- after the app registry is ready -- and only
        when tom_targets is installed. A TOM without it simply never gets the
        signals, which is correct: the plugin owns its own integration, and the
        geometry / skymap features do not need tom_targets at all.
        """
        try:
            from tom_targets.models import Target
        except Exception:
            # tom_targets not installed (e.g. the standalone test boot).
            return

        from django.db.models.signals import post_delete, post_save

        from tom_regions.signals import delete_target_healpix, update_target_healpix

        post_save.connect(
            update_target_healpix,
            sender=Target,
            dispatch_uid="tom_regions_target_healpix_save",
        )
        post_delete.connect(
            delete_target_healpix,
            sender=Target,
            dispatch_uid="tom_regions_target_healpix_delete",
        )

    # ------------------------------------------------------------------
    # TOMToolkit plugin hooks. Phase 1 ships intentional no-ops; Phase 2
    # rewrites these to point at the real Region list/detail views, the
    # navbar partial, and the Aladin overlay.
    # ------------------------------------------------------------------

    def target_detail_buttons(self):
        # The plan calls for a "Find regions containing this target"
        # button that routes to ``regions:list?cone_search=<ra>,<dec>,5deg``.
        # That requires either query-string-aware button rendering in
        # tom_common (currently the button helper just runs ``{% url %}``
        # against the namespace) or a dedicated detail view that takes
        # the target id and computes the cone server-side. Both options
        # are bigger than Phase 2 wants to chew. Returning None disables
        # the button cleanly until we can do this properly in Phase 4
        # alongside the targets-in-region API view.
        return None

    def nav_items(self):
        # The "Regions" link is contributed via this partial; tom_common's
        # ``{% navbar_app_addons %}`` template tag iterates each installed
        # app's nav_items() and renders the returned partials in order.
        # ``position`` defaults to "right" if omitted; we explicitly want
        # "left" since the link is a primary navigation target rather
        # than a user/account utility.
        return [{"partial": f"{self.name}/partials/navbar_regions.html", "position": "left"}]

    def include_url_paths(self):
        # The URL include is wired up now even though urls.py exposes no
        # routes yet. That way Phase 2 can land routes by editing one file.
        return [path("regions/", include("tom_regions.urls", namespace="regions"))]

    def profile_details(self):
        # tom_regions intentionally does not own a per-user profile section.
        return []
