"""Views for tom_regions.

Where this fits in the Phase 2 view stack
-----------------------------------------
The list / detail / MOC-JSON views are the third leg of the Phase 2
trio (after :mod:`tables` and :mod:`filters`). They tie a URL pattern
to a queryset, a Table class, and a FilterSet class, and they render
the results into the templates we'll write next.

Three concrete views:

- :class:`RegionListView` -- HTMX-driven, paginated list. Mirrors
  :class:`tom_targets.views.TargetListView` in shape; the only
  region-specific bits are which Table / FilterSet it uses and what
  it puts in the ``skymap_objects`` context (used by the Aladin
  template tag we'll write later).
- :class:`RegionDetailView` -- single-region landing page. Plain
  Django ``DetailView``; the Aladin overlay there is one MOC instead
  of many.
- :class:`RegionMOCJsonView` -- a small JSON endpoint Aladin Lite
  fetches via :js:func:`aladin.addMOCFromURL`. See its docstring for
  the full IVOA-MOC-JSON contract; this is the seam through which
  mocpy hands a region's geometry off to the browser.

Phase 2 ships read-only views; the create/update/delete views land in
Phase 3 with the form work. The plugin hook in ``apps.py`` already
wires the URL include, so adding routes here is the only edit needed
when each new view comes online.
"""

from __future__ import annotations

from collections import defaultdict

from django.http import JsonResponse
from django.views.generic import DetailView, View
from django.shortcuts import get_object_or_404
from django_filters.views import FilterView

from tom_common.htmx_table import HTMXTableViewMixin
from tom_regions.filters import RegionFilterSet
from tom_regions.healpix_django.encoding import range_to_level_ipix
from tom_regions.models import Region, RegionTile
from tom_regions.tables import RegionTable


class RegionListView(HTMXTableViewMixin, FilterView):
    """HTMX-driven, filterable list of regions.

    The class hierarchy here is worth pausing on:

    - :class:`django_filters.views.FilterView` calls
      ``RegionFilterSet`` against the GET parameters and exposes the
      filtered queryset.
    - :class:`tom_common.htmx_table.HTMXTableViewMixin` (a
      :class:`django_tables2.SingleTableMixin` subclass) wraps the
      queryset in ``RegionTable`` and adds the partial-template
      switcheroo: htmx requests get only the table body, regular
      requests get the full ``region_list.html``.

    The combination yields a list view where every filter change, sort
    click, or page link makes a small targeted GET via htmx that
    re-renders only the rows. We never reload the page.

    ``skymap_objects`` in the context
    ---------------------------------
    The Aladin template tag we'll write in Phase 2's template step
    expects a list of region objects to overlay on the sky. Stuffing
    them into the context here keeps the template logic simple
    (``{% aladin_region_skymap skymap_objects %}``) and lets us tune
    the slice without touching templates -- right now we publish the
    *current page* of the table so the overlay matches what the user
    sees in the rows.
    """

    template_name = "tom_regions/region_list.html"
    paginate_by = 20
    model = Region
    filterset_class = RegionFilterSet
    table_class = RegionTable
    ordering = ["-created"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # ``object_list`` is the post-filter, post-pagination queryset
        # already attached by FilterView + django-tables2.
        context["region_count"] = context["record_count"]
        context["skymap_objects"] = list(context["object_list"])
        return context


class RegionDetailView(DetailView):
    """Single-region landing page.

    Plain Django ``DetailView``. The template will reuse the same
    Aladin partial as the list view, just passing a one-element list
    so a single MOC ends up on the sky.
    """

    template_name = "tom_regions/region_detail.html"
    model = Region


class RegionMOCJsonView(View):
    """Serve a region's geometry as IVOA-style MOC JSON for Aladin Lite.

    Why this view exists
    --------------------
    Aladin Lite v3 draws a MOC overlay from a JSON document of the
    shape::

        {"<order>": [<ipix>, <ipix>, ...], ...}

    This is the IVOA Multi-Order Coverage JSON serialization
    (https://ivoa.net/documents/MOC/) -- one key per HEALPix order,
    each value a list of NESTED pixel indices at that order. mocpy
    speaks the same dialect: ``MOC.serialize(format='json')`` returns
    exactly this dict, and ``MOC.from_json(...)`` parses it back.

    The mocpy <-> Aladin coordination
    ---------------------------------
    Putting those two facts together gives the round-trip we depend on::

        DB rows (int8range tiles)              JSON dict
              |                                     ^
              v                                     |
        Region.to_moc() ---> mocpy.MOC -------------+
                              |                serialize(format='json')
                              v
                        addMOCFromURL ------------> aladin.js v3
                          (this view's URL)        (renders overlay)

    Both ends are talking the same standard, so this view is a thin
    HTTP wrapper: rebuild the MOC from the tile rows, ask mocpy to
    serialize, hand the dict to JsonResponse. There is no custom
    encoding logic here, and there is intentionally none -- if a
    future version of Aladin or mocpy diverges from the IVOA shape,
    the right fix is in mocpy or in this view, not in a hand-rolled
    encoder spread across the codebase.

    Performance / scaling note
    --------------------------
    For ordinary regions (a few hundred tiles) the full reconstructed
    MOC fits in the response easily. LIGO-scale skymaps (50k+ tiles)
    will want streaming or a more compact wire format; that is a
    Phase 4 concern when we ingest real localization maps.
    """

    def get(self, request, pk: int):
        # We need the (level, ipix) pair for each tile. Doing the
        # decoding in Python here rather than calling Region.to_moc()
        # avoids two round-trips through mocpy and is straightforward
        # at v1 sizes (a few hundred tiles per region). If/when a
        # region ever holds 50k+ tiles, switching to ``Region.to_moc().
        # serialize(format='json')`` is a one-liner.
        get_object_or_404(Region, pk=pk)  # 404 if missing
        rows = RegionTile.objects.filter(region_id=pk).values_list("hpx", flat=True)
        # IVOA MOC JSON shape: {"<order>": [<ipix>, ...], ...}.
        moc_json: dict[str, list[int]] = defaultdict(list)
        # Tiles in our table are deepest-level int8range intervals;
        # decode each to (level, ipix). Each tile decodes uniquely
        # because bulk_create_tiles always emits NESTED-aligned ranges.
        for hpx in rows:
            level, ipix = range_to_level_ipix(hpx.lower, hpx.upper)
            moc_json[str(level)].append(ipix)
        return JsonResponse(moc_json)
