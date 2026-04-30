"""Template tags for the regions list/detail templates.

Where this fits in the Phase 2 view stack
-----------------------------------------
The list and detail templates render the Aladin Lite v3 sky-map by
delegating to the inclusion tags here. There are two of them, mirroring
the tom_targets pattern:

- :func:`aladin_region_skymap` -- emits the full ``<div>`` plus the
  init script. Used once per page on the initial server render.
- :func:`aladin_region_skymap_oob` -- emits a lightweight OOB
  ``<script>`` block that re-runs the per-region MOC loader against an
  existing viewer. Used in HTMX partial responses so a filter change
  updates the overlay without rebuilding the whole Aladin canvas.

Both tags hand the templates a list of ``{id, name, moc_url, color}``
dicts, JSON-encoded for direct embedding in the JS. The browser
fetches each ``moc_url`` (the :class:`tom_regions.views.RegionMOCJsonView`
endpoint) and Aladin renders the returned IVOA-MOC-JSON natively. See
:class:`tom_regions.views.RegionMOCJsonView` for the mocpy <-> Aladin
contract.

Why a small color palette
-------------------------
Aladin draws each MOC as a translucent overlay; a single shared color
makes overlapping regions hard to disambiguate. We cycle through a
small qualitative palette (Tableau-10-style) so adjacent regions in
the list get different hues. Cycling rather than hashing keeps the
choice deterministic relative to the filter result order, which is
useful when a user is comparing regions across page loads.
"""

from __future__ import annotations

import json

from django import template
from django.urls import reverse

register = template.Library()


# A short qualitative palette. Eight is enough to disambiguate the
# typical 20-region page; if a TOM regularly has more than that on
# screen, a hashing strategy would be worth revisiting.
_REGION_COLORS = [
    "#1f77b4",  # blue
    "#ff7f0e",  # orange
    "#2ca02c",  # green
    "#d62728",  # red
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#17becf",  # cyan
]


def _regions_to_json(regions) -> str:
    """Serialize a region iterable to the JSON shape Aladin needs.

    The shape ``[{id, name, moc_url, moc_json, color}, ...]`` is what
    both ``aladin_region_skymap.html`` (initial render) and
    ``aladin_region_skymap_oob.html`` (HTMX OOB update) consume.

    Why ``moc_json`` is inlined alongside ``moc_url``
    -------------------------------------------------
    Aladin Lite v3 has two MOC factories:
    :js:func:`A.MOCFromURL` (FITS-only) and :js:func:`A.MOCFromJSON`
    (in-memory IVOA JSON dict). Our HTTP endpoint serves IVOA JSON, so
    the URL path would need a FITS endpoint to pair with MOCFromURL --
    that is Phase 4 work. For now we inline the JSON dict on initial
    render (``MOCFromJSON``) and keep ``moc_url`` available for the
    detail-page link and external IVOA tools.

    Consequence: the per-region payload grows with the tile count.
    Fine for the typical few-hundred-tile region; LIGO-scale skymaps
    (50k+ tiles per region) will want either a FITS endpoint or
    chunked loading. Flagged in :func:`region_to_moc_json`.

    Keeping the serialization in one helper means the initial-render
    and OOB-update paths can never drift apart -- a frequent source
    of "filter update half-works" bugs.
    """
    from tom_regions.utils import region_to_moc_json

    payload = []
    for index, region in enumerate(regions):
        payload.append(
            {
                "id": region.pk,
                "name": region.name,
                "moc_url": reverse("regions:moc-json", kwargs={"pk": region.pk}),
                "moc_json": region_to_moc_json(region),
                "color": _REGION_COLORS[index % len(_REGION_COLORS)],
            }
        )
    return json.dumps(payload)


@register.inclusion_tag("tom_regions/partials/aladin_region_skymap.html")
def aladin_region_skymap(regions):
    """Render the initial Aladin viewer with one MOC overlay per region.

    Use this once on the page's initial server render. For subsequent
    HTMX-driven filter changes, use :func:`aladin_region_skymap_oob`,
    which side-loads new region data into the existing viewer rather
    than reinitializing it.
    """
    return {"regions_json": _regions_to_json(regions)}


@register.inclusion_tag("tom_regions/partials/aladin_region_skymap_oob.html")
def aladin_region_skymap_oob(regions, remove_name: str = ""):
    """Emit an OOB script updating the Aladin overlay to a new region set.

    Mirrors :func:`tom_targets.templatetags.targets_extras.aladin_skymap_targets_oob`.
    The HTMX response includes both the updated table body partial and
    this OOB block; the browser swaps the table contents in place and
    runs the script, which calls ``window.updateAladinRegions`` (set up
    by the initial-render template) to refresh the overlay.

    The optional ``remove_name`` argument is the Aladin overlay name
    to drop from the canvas before applying the update. The "Save MOC"
    flow uses it to clear the user's pre-save (auto-named) MOC overlay
    once the saved version is about to be added back under the user's
    chosen name. The regular filter-update path leaves it empty and
    the JS skips the remove step.
    """
    return {
        "regions_json": _regions_to_json(regions),
        "remove_name": remove_name,
    }
