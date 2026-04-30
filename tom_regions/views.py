"""Views for tom_regions.

Where this fits in the view stack
---------------------------------
Phase 2 shipped the read-only triad: list, detail, and the MOC-JSON
endpoint Aladin fetches per region. Phase 3 adds the create flow:
:class:`RegionCreateView` dispatches to one of three forms based on a
``?type=`` query parameter (polygon / circle / moc) so each mode shows
only the inputs that mode actually needs. The forms themselves
(:mod:`tom_regions.forms`) own the mode-specific MOC construction;
this module just routes.

Five concrete views:

- :class:`RegionListView` -- HTMX-driven, paginated list. Mirrors
  :class:`tom_targets.views.TargetListView` in shape.
- :class:`RegionDetailView` -- single-region landing page.
- :class:`RegionMOCJsonView` -- IVOA-MOC-JSON endpoint Aladin Lite
  fetches via :js:func:`A.MOCFromJSON`. See its docstring for the
  mocpy-Aladin contract.
- :class:`RegionCreateView` -- Phase 3 create dispatcher; chooses the
  right form by ``?type=`` and stamps the request user as the creator.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DetailView, View
from django_filters.views import FilterView

from tom_common.htmx_table import HTMXTableViewMixin
from tom_regions.filters import RegionFilterSet
from tom_regions.forms import RegionFromAladinForm, RegionMOCUploadForm
from tom_regions.models import Region
from tom_regions.tables import RegionTable
from tom_regions.utils import region_to_moc_json


# The ``?type=`` query parameter chooses the form class for
# :class:`RegionCreateView`. The Aladin-driven save flow lives on the
# list page itself (see :class:`RegionSaveFromAladinView`), so the
# create-page dispatcher here is just for the FITS upload mode.
# Keeping it as a map (rather than collapsing to a single class) leaves
# room for future modes (e.g., catalog-from-vizier) without a control-
# flow refactor.
_CREATE_FORM_BY_TYPE = {
    "upload": RegionMOCUploadForm,
}
_DEFAULT_CREATE_TYPE = "upload"


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
        import json

        context = super().get_context_data(**kwargs)
        # ``object_list`` is the post-filter, post-pagination queryset
        # already attached by FilterView + django-tables2.
        context["region_count"] = context["record_count"]
        context["skymap_objects"] = list(context["object_list"])
        # The Save-MOC form's name pre-fill check needs the current set
        # of Region.name values so it can avoid pre-filling a name that
        # already exists. Snapshot at page load; the server-side
        # uniqueness constraint handles staleness on save.
        context["existing_region_names_json"] = json.dumps(
            list(Region.objects.values_list("name", flat=True))
        )
        return context


class RegionDetailView(DetailView):
    """Single-region landing page.

    Plain Django ``DetailView``. The template reuses the list view's
    Aladin partial by passing a one-element iterable as
    ``region_singleton``, which keeps the template tag's contract
    (it always receives an iterable of regions) consistent across
    the list and detail pages.
    """

    template_name = "tom_regions/region_detail.html"
    model = Region

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["region_singleton"] = [self.object]
        return context


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
        # The encoding logic lives in tom_regions.utils.region_to_moc_json
        # so the in-page Aladin overlay (which inlines the same dict)
        # cannot drift from this HTTP endpoint. See that helper's
        # docstring for the IVOA shape and the rationale.
        region = get_object_or_404(Region, pk=pk)
        return JsonResponse(region_to_moc_json(region))


class RegionCreateView(LoginRequiredMixin, CreateView):
    """Phase 3 create dispatcher: one URL, three modes.

    The ``?type=`` query parameter selects the form class via the
    :data:`_CREATE_FORM_BY_TYPE` map. Modes:

    - ``polygon`` -- the user clicks vertices in Aladin Lite via the
      ``aladin.select('poly', cb)`` selection tool; the JS bridge in
      ``aladin_draw_controls.html`` writes them into a hidden field.
      The server builds the MOC via ``MOC.from_polygon_skycoord``.
    - ``circle`` -- three numeric inputs (RA, Dec, radius) drive
      ``MOC.from_cone``. No JS bridge needed.
    - ``moc`` -- file upload; ``MOC.from_fits`` parses it.

    Why dispatch by query param vs. by URL path
    -------------------------------------------
    Three URL-named routes (``regions:create-polygon``, etc.) would
    work just as well, but the ``?type=`` form keeps the URL space
    flat: ``/regions/create/`` is "the create page," and the mode is
    a parameter rather than a separate page. The list view's "Create
    Region" dropdown still surfaces the three options as distinct
    items; they just resolve to the same path with different query
    strings.

    The ``regions:create`` URL is unconditionally login-required;
    creating geometry is a write action and tom_regions doesn't have
    anonymous-write semantics.
    """

    template_name = "tom_regions/region_form.html"
    # ``model`` and ``form_class`` are normally set as class
    # attributes on a CreateView. We override ``get_form_class`` to
    # pick the form per request, so neither attribute is set here.
    model = Region

    def _resolve_type(self) -> str:
        """Resolve the ``?type=`` parameter, falling back to the default.

        Read from POST first so a form submission always uses the same
        class as the GET that rendered the form, even if the form
        action URL accidentally drops the query string. Falls back to
        GET, then to the default.
        """
        return (
            self.request.POST.get("type")
            or self.request.GET.get("type")
            or _DEFAULT_CREATE_TYPE
        )

    def get_form_class(self):
        # Falls back to the upload form (the only mode currently in the
        # map) for any unknown ``?type=`` value. The Aladin-driven save
        # flow lives on the list page itself rather than here.
        return _CREATE_FORM_BY_TYPE.get(self._resolve_type(), RegionMOCUploadForm)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The template branches on ``region_type`` to show the right
        # input set. The full type list is also exposed so the template
        # can render mode-switching links.
        context["region_type"] = self._resolve_type()
        context["region_type_choices"] = list(_CREATE_FORM_BY_TYPE.keys())
        # Polygon mode shows the Aladin canvas (so the user can draw),
        # but no MOCs are overlaid -- we want the bare sky as a drawing
        # surface. The template tag accepts any iterable of Region
        # objects, including an empty list.
        context["empty_skymap"] = []
        return context

    def form_valid(self, form):
        # Stamp the creator before the form's save() runs the MOC
        # construction. The form's save chain is:
        #   form.save() -> super().save(commit=False) -> form.region_type
        #   stamp -> region.save() -> bulk_create_tiles.
        # We set creator on the unsaved instance here so it survives
        # into region.save() without a follow-up update.
        form.instance.creator = self.request.user if self.request.user.is_authenticated else None
        return super().form_valid(form)

    def get_success_url(self):
        # Land on the new region's detail page so the user immediately
        # sees the rendered MOC overlay and confirms the save did what
        # they meant.
        return reverse("regions:detail", kwargs={"pk": self.object.pk})


class RegionSaveFromAladinView(LoginRequiredMixin, View):
    """Persist a MOC drawn in Aladin Lite, in-place on the list page.

    The list page hosts an HTMX-driven "Save MOC" form section whose
    submit posts here. The handler:

    1. Validates the form (name, description, IVOA-JSON dict).
    2. Persists the Region via :class:`RegionFromAladinForm.save`,
       which ultimately calls :func:`bulk_create_tiles`.
    3. Returns an HTMX response that:
       - Swaps the table partial in place so the new row appears.
       - Carries an OOB block telling the browser to remove the
         pre-save Aladin overlay (whose name is in the form payload)
         and add the saved Region's overlay back under the user's
         chosen name.

    Why a dedicated view rather than reusing RegionCreateView
    ----------------------------------------------------------
    The list-page save flow has different success semantics: it must
    return the table partial (HTMX) instead of redirecting to the
    detail page, and it has to emit the Aladin OOB script. Those are
    sufficiently different from the upload flow's "redirect to detail"
    semantics that splitting them keeps each handler readable.
    """

    def post(self, request, *args, **kwargs):
        form = RegionFromAladinForm(request.POST)
        # Stamp the creator before save() so it survives into
        # bulk_create_tiles' context.
        if form.is_valid():
            form.instance.creator = request.user if request.user.is_authenticated else None
            region = form.save()
            return self._render_success(request, region)
        return self._render_failure(request, form)

    def _render_success(self, request, region: Region):
        """HTMX response: updated table partial (carrying its own Aladin OOB).

        The table partial includes a call to the
        :func:`aladin_region_skymap_oob` template tag, which emits the
        OOB block that updates Aladin's overlay set. We pass
        ``aladin_overlay_name_to_remove`` through context so that tag
        also tells the browser to drop the user's pre-save (auto-
        named) overlay before the saved version is added back -- in
        a single HTMX response.
        """
        from django.http import HttpResponse
        from django.template.loader import render_to_string

        from tom_regions.tables import RegionTable

        # Re-render the table partial with the full current page so the
        # new row joins its peers. The list view's filter/pagination
        # state is not preserved here in v1; the user lands on page 1
        # of an unfiltered view. A more refined version would carry
        # over the filter form state by sending the filter values
        # along with the save POST. Deferred.
        table_objects = list(Region.objects.all().order_by("-created")[:20])
        table = RegionTable(table_objects)

        context = {
            "table": table,
            "empty_database": False,
            "skymap_objects": table_objects,
            # Picked up by region_table_partial.html and passed
            # through to the aladin_region_skymap_oob template tag.
            "aladin_overlay_name_to_remove": request.POST.get(
                "aladin_overlay_name", ""
            ),
        }
        body = render_to_string(
            "tom_regions/partials/region_table_partial.html",
            context,
            request=request,
        )
        return HttpResponse(body)

    def _render_failure(self, request, form):
        """HTMX response on validation failure: re-render the form section.

        We surface form errors inline so the user can fix and retry
        without losing their Aladin overlay state. The form section
        partial is responsible for showing the field-level errors.
        """
        from django.http import HttpResponseBadRequest
        from django.template.loader import render_to_string

        body = render_to_string(
            "tom_regions/partials/aladin_save_moc_form.html",
            {"save_moc_form": form, "save_moc_form_errors": form.errors},
            request=request,
        )
        return HttpResponseBadRequest(body)
