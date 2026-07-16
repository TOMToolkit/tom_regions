"""django-filter FilterSet for the Region list view.

Where this fits in the Phase 2 view stack
-----------------------------------------
The list view binds three pieces together::

    URL -> View (HTMXTableViewMixin + FilterView)
              |
              +-- Table     (tables.py: rows -> HTML cells)
              +-- FilterSet (this module: GET params -> queryset)
              +-- Templates (region_list.html and partials)

The FilterSet is the GET-params side of the contract. django-filter
takes the form-encoded query string, validates each named field
against a Filter declared on the FilterSet, and returns a filtered
queryset. :class:`tom_common.htmx_table.HTMXTableFilterSet` adds the
HTMX glue: a debounced "general search" input, an override point for
the search function, and a crispy-forms helper that drops the submit
button (htmx fires on input change instead).

Filter taxonomy
---------------
The advanced filters fall into three groups, each backed by a
different SQL pattern. Knowing which group a filter is in tells you
what the resulting query looks like and how it scales:

1. **Scalar filters** -- ``name`` (icontains), ``type`` (choice),
   ``area_sr`` (range). Standard django-filter, nothing exotic. These
   compile to plain ``WHERE column op value`` clauses on
   ``regions_region``.
2. **Single-point filters** -- ``contains_point`` (RA, Dec) and
   ``contains_target`` (target name). Both compute one deepest-level
   pixel index and emit the inherited ``int8range @> bigint`` lookup
   from :class:`tom_regions.healpix_django.HealpixTileField`. They
   differ only in where the (RA, Dec) comes from. Each compiles to
   one JOIN to ``regions_regiontile`` plus a single SP-GiST-friendly
   predicate.
3. **Cone-search filter** -- ``cone_search`` (RA, Dec, radius). Builds
   a MOC for the cone via ``mocpy.MOC.from_cone`` and matches regions
   whose tiles overlap any tile of the cone. v1 uses an OR-across-cone-
   tiles query (one ``hpx && range`` predicate per cone tile),
   which produces a clean SQL ``WHERE ... OR ... OR ...`` chain. Fine
   for the few-hundred-tile MOCs typical of single-pointing searches;
   large cones (thousands of tiles) will warrant an
   ``int8range && int8multirange`` form, deferred to Phase 4 alongside
   an ``Int8MultiRangeField``.
"""

from __future__ import annotations

import logging

import django_filters
from astropy import units as u
from astropy.coordinates import Angle, Latitude, Longitude, SkyCoord
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Layout, Row
from django import forms
from django.db.models import Q

from tom_common.htmx_table import HTMXTableFilterSet
from tom_regions.base_models import REGION_TYPE_CHOICES
from tom_regions.healpix_django.encoding import skycoord_to_point
from tom_regions.models import Region, RegionList

logger = logging.getLogger(__name__)


# Common HTMX widget attributes for "search-on-Enter" filters (those whose
# inputs aren't useful until the user has typed everything). RA/Dec/radius
# triplets fall into this category; partial input would mis-fire a search.
_HTMX_ON_ENTER = {
    "hx-get": "",
    "hx-trigger": "keyup[keyCode==13]",
    "hx-target": "div.table-container",
    "hx-swap": "innerHTML",
    "hx-indicator": ".progress",
    "hx-include": "closest form",
}

# Common HTMX widget attributes for "search-on-change" filters (selects).
_HTMX_ON_CHANGE = {
    "hx-get": "",
    "hx-trigger": "change",
    "hx-target": "div.table-container",
    "hx-swap": "innerHTML",
    "hx-indicator": ".progress",
    "hx-include": "closest form",
}


class RegionFilterSet(HTMXTableFilterSet):
    """Filters available on the Region list view.

    Public filters (in roughly the order they appear in the form):

    - ``query`` -- general icontains search across ``name`` and
      ``description`` (overridden ``general_search``).
    - ``name`` -- name icontains. Hidden behind the advanced toggle but
      part of the API.
    - ``type`` -- one of the :data:`REGION_TYPE_CHOICES`.
    - ``contains_point`` -- "RA, Dec" string; matches regions whose
      tiles cover that single sky pixel.
    - ``contains_target`` -- a Target name; same query but with the
      pixel sourced from the target's row in the database.
    - ``cone_search`` -- "RA, Dec, radius_deg" string; matches regions
      whose tiles overlap any tile of the cone MOC.
    - ``area_sr_min`` / ``area_sr_max`` -- bound the cached area in
      steradians.
    """

    # ------------------------------------------------------------------
    # Scalar filters.
    # ------------------------------------------------------------------

    name = django_filters.CharFilter(
        field_name="name",
        lookup_expr="icontains",
        label="Name contains",
    )

    type = django_filters.ChoiceFilter(
        choices=REGION_TYPE_CHOICES,
        widget=forms.Select(attrs=_HTMX_ON_CHANGE),
    )

    # ``regionlist__name`` is kept as the URL parameter / Python
    # attribute name to match the tom_targets convention (so a URL
    # like /regions/?regionlist__name=42 reads the same as
    # /targets/?targetlist__name=42). The actual ORM lookup is on
    # ``region_lists`` -- the related_name we set on the M2M, which
    # Django uses for both the reverse accessor and reverse lookups.
    # The form sends a RegionList instance; Django's ORM coerces that
    # to a pk for the M2M membership test.
    regionlist__name = django_filters.ModelChoiceFilter(
        field_name="region_lists",
        queryset=lambda request: (
            RegionList.objects.all()
            if request.user.is_authenticated
            else RegionList.objects.none()
        ),
        label="Region Group",
        widget=forms.Select(attrs=_HTMX_ON_CHANGE),
    )

    # Area inputs are deg² (the astronomer-facing unit). The DB
    # column is steradians, so each filter method does the conversion
    # before applying the lookup. Using ``method=`` lets us keep the
    # URL parameter / form-field name in deg² while the ORM stays in
    # SI.
    area_min_deg2 = django_filters.NumberFilter(
        method="filter_area_min_deg2",
        label="Min area (deg²)",
    )
    area_max_deg2 = django_filters.NumberFilter(
        method="filter_area_max_deg2",
        label="Max area (deg²)",
    )

    @staticmethod
    def _deg2_to_sr(value):
        from tom_regions.healpix_django.constants import SQ_DEG_PER_STERADIAN

        # ``NumberFilter`` cleans the input to a Decimal; coerce to
        # float before dividing by our (float) constant.
        return float(value) / SQ_DEG_PER_STERADIAN

    def filter_area_min_deg2(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(area_sr__gte=self._deg2_to_sr(value))

    def filter_area_max_deg2(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(area_sr__lte=self._deg2_to_sr(value))

    # ------------------------------------------------------------------
    # Single-point filters: "which regions cover this point?"
    # ------------------------------------------------------------------

    contains_point = django_filters.CharFilter(
        method="filter_contains_point",
        label="Contains point",
        help_text="RA, Dec (degrees)",
        widget=forms.TextInput(attrs={"placeholder": "RA, Dec", **_HTMX_ON_ENTER}),
    )

    contains_target = django_filters.CharFilter(
        method="filter_contains_target",
        label="Contains target",
        help_text="Target name (or alias) -- finds regions covering its position",
        widget=forms.TextInput(attrs={"placeholder": "Target name", **_HTMX_ON_ENTER}),
    )

    def filter_contains_point(self, queryset, name, value):
        """Filter to regions whose tiles contain the deepest-level pixel for (RA, Dec)."""
        if not value:
            return queryset
        try:
            ra_str, dec_str = (s.strip() for s in value.split(","))
            sc = SkyCoord(float(ra_str), float(dec_str), unit="deg")
        except (ValueError, AttributeError):
            logger.debug("filter_contains_point: cannot parse %r", value)
            return queryset.none()
        return queryset.filter(tiles__hpx__contains=skycoord_to_point(sc)).distinct()

    def filter_contains_target(self, queryset, name, value):
        """Look up a Target by name/alias, then filter to regions covering it.

        We mirror tom_targets' fuzzy-name lookup (``Target.matches``) so a
        user can paste a TNS name or an LCO internal alias and get the
        same result they'd see on the targets page. Ambiguous matches
        (more than one target with that name) return no regions; the
        user has to disambiguate.
        """
        if not value:
            return queryset
        try:
            from tom_targets.models import Target
        except ImportError:
            logger.warning("contains_target filter unusable: tom_targets not installed")
            return queryset.none()
        # ``Target.matches.match_target`` performs a name+alias lookup and
        # returns a queryset; we resolve to a single hit before computing
        # the pixel.
        target_qs = Target.matches.match_target(value)
        targets = list(target_qs[:2])  # cap the slice; one row is enough
        if len(targets) != 1:
            return queryset.none()
        target = targets[0]
        sc = SkyCoord(target.ra, target.dec, unit="deg")
        return queryset.filter(tiles__hpx__contains=skycoord_to_point(sc)).distinct()

    # ------------------------------------------------------------------
    # Cone-search filter: "which regions overlap this cone?"
    # ------------------------------------------------------------------

    cone_search = django_filters.CharFilter(
        method="filter_cone_search",
        label="Cone search (overlapping regions)",
        help_text="RA, Dec, radius (degrees)",
        widget=forms.TextInput(
            attrs={"placeholder": "RA, Dec, Radius", **_HTMX_ON_ENTER}
        ),
    )

    def filter_cone_search(self, queryset, name, value):
        """Filter to regions whose tiles overlap any tile of the cone.

        Builds a MOC for the cone, decomposes it to (lower, upper) tile
        intervals, and emits an OR over ``hpx__overlap`` for each. For
        the cone sizes typical of Phase 1/2 (a few degrees, ~100 tiles)
        this is fine; large skymap cross-matches (LIGO-scale) want a
        single ``int8range && int8multirange`` query, which we'll add
        in Phase 4 alongside an ``Int8MultiRangeField``.
        """
        if not value:
            return queryset
        try:
            ra_str, dec_str, radius_str = (s.strip() for s in value.split(","))
            ra_deg = float(ra_str)
            dec_deg = float(dec_str)
            radius_deg = float(radius_str)
        except (ValueError, AttributeError):
            logger.debug("filter_cone_search: cannot parse %r", value)
            return queryset.none()

        # Heavy import; kept inside the method so general filter use
        # doesn't pay mocpy's import cost on every form render.
        from mocpy import MOC

        from tom_regions.healpix_django.encoding import moc_to_ranges

        cone = MOC.from_cone(
            lon=Longitude(ra_deg * u.deg),
            lat=Latitude(dec_deg * u.deg),
            radius=Angle(radius_deg * u.deg),
            max_depth=10,
        )
        q = Q()
        for lower, upper in moc_to_ranges(cone):
            q |= Q(tiles__hpx__overlap=(lower, upper))
        if not q:  # empty cone (zero radius)
            return queryset.none()
        return queryset.filter(q).distinct()

    # ------------------------------------------------------------------
    # General search and form layout.
    # ------------------------------------------------------------------

    def general_search(self, queryset, name, value):
        """Search the canonical name and the description.

        We deliberately do not search ``RegionName`` aliases here in
        Phase 2; the alias table will be wired up in Phase 3 alongside
        the create flow that populates it. Anyone needing alias search
        in v1 can use the ``name`` filter, which the API exposes.
        """
        if not value:
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))

    @property
    def form(self):
        """Crispy-forms layout for the filter form.

        Two design choices worth flagging:

        1. We disable the ``<form>`` tag (``form_tag = False``). The
           list template renders one outer form that contains the
           filter inputs, the table itself, and the per-row checkbox
           group. Letting crispy-forms emit its own form tag would
           nest forms, which HTML doesn't allow.

        2. We disable CSRF (``disable_csrf = True``). The filter form
           submits via htmx GET requests, which Django doesn't require
           CSRF for (CSRF is a write-side defense). The mutating
           actions on this page (delete-selected, group-add) live in
           a separate form that does include CSRF.

        The cached ``_form`` attribute is the same pattern
        :class:`HTMXTableFilterSet` uses; we have to re-cache it here
        because we want our own helper rather than the parent's.
        """
        if not hasattr(self, "_form"):
            self._form = super().form
            helper = FormHelper()
            helper.form_tag = False
            helper.disable_csrf = True
            helper.form_show_labels = True

            # If the user arrived with any advanced filter already
            # populated in the URL, leave the Advanced section open so
            # they can see what's filtering. Otherwise fold it for a
            # clean default appearance. ``self.data`` is the bound GET
            # QueryDict; an empty value (None or "") doesn't count as
            # "in use."
            advanced_in_use = any(
                self._form.data.get(name) for name in self.Meta.fields
            )
            collapse_class = "collapse show" if advanced_in_use else "collapse"
            aria_expanded = "true" if advanced_in_use else "false"

            # The Advanced toggle link's class controls Bootstrap's
            # chevron / state CSS hooks: the ``collapsed`` class flags
            # "currently folded" so any future styling can rotate a
            # caret consistently with the save-MOC card.
            toggle_html = (
                f'<div class="row"><div class="col-md-12 mb-2">'
                f'<a class="btn btn-link p-0'
                f'{"" if advanced_in_use else " collapsed"}" '
                f'data-toggle="collapse" href="#advancedFilters" role="button" '
                f'aria-expanded="{aria_expanded}" aria-controls="advancedFilters">'
                f'Advanced &rsaquo;</a></div></div>'
            )

            helper.layout = Layout(
                Row(Column("query", css_class="form-group col-md-3")),
                HTML(toggle_html),
                Div(
                    Row(
                        Column("name", css_class="form-group col-md-4"),
                        Column("type", css_class="form-group col-md-4"),
                        Column("regionlist__name", css_class="form-group col-md-4"),
                    ),
                    Row(
                        Column("contains_point", css_class="form-group col-md-6"),
                        Column("contains_target", css_class="form-group col-md-6"),
                    ),
                    Row(Column("cone_search", css_class="form-group col-md-12")),
                    Row(
                        Column("area_min_deg2", css_class="form-group col-md-3"),
                        Column("area_max_deg2", css_class="form-group col-md-3"),
                    ),
                    css_class=collapse_class,
                    css_id="advancedFilters",
                ),
            )
            self._form.helper = helper
        return self._form

    class Meta:
        model = Region
        fields = [
            "name",
            "type",
            "regionlist__name",
            "contains_point",
            "contains_target",
            "cone_search",
            "area_min_deg2",
            "area_max_deg2",
        ]


class RegionGroupFilterSet(HTMXTableFilterSet):
    """Bare-bones FilterSet for the Region Grouping list page.

    Inherits the ``query`` general-search field from
    :class:`HTMXTableFilterSet` and adds nothing. Mirrors
    :class:`tom_targets.filters.TargetGroupFilterSet`.
    """

    class Meta:
        model = RegionList
        fields: list = []
