"""Context providers for tom_base target-detail integration points.

The functions here are referenced by dotted path from
:class:`tom_regions.apps.TomRegionsConfig` (its ``target_detail_tabs`` method)
and are called by tom_base's ``get_app_tabs`` template tag with the target
detail page's template context. Each returns a dict that renders the
accompanying partial.

Keeping them here -- rather than in ``apps.py`` or a templatetags module -- keeps
``apps.py`` to wiring and gives the cross-app integration code one obvious home.
"""

from __future__ import annotations

from typing import Any


def _target_from_context(context: Any):
    """Pull the Target off the target-detail page context.

    The detail page is a Django ``DetailView``, so the object is exposed as
    ``object`` (and, for tom_targets, also ``target``). We accept either.
    """
    return context.get("object") or context.get("target")


def target_regions_tab_context(context: Any) -> dict:
    """Context for the "Regions" tab: the regions whose footprint covers the target."""
    from tom_regions.services.queries import regions_containing_target

    target = _target_from_context(context)
    regions = regions_containing_target(target) if target is not None else []
    return {"target": target, "regions": regions}
