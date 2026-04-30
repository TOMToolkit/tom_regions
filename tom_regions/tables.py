"""django-tables2 table for the Region list view.

This is the first piece of the Phase 2 view stack. The full stack is::

    URL -> View (HTMXTableViewMixin + FilterView)
              |
              +-- Table  (this module: rows -> HTML cells)
              +-- FilterSet (filters.py: GET params -> queryset)
              +-- Templates (region_list.html and partials)

The pieces are glued together by django_tables2 + django_filter +
HTMX. When the user changes a filter input, htmx fires a GET request
including the form, the view reapplies the filter set, the table
re-renders only its body partial, and the response replaces the
``div.table-container`` element in place. No full page reload, no JS
state to manage.

What :class:`tom_common.htmx_table.HTMXTable` provides for free
----------------------------------------------------------------
- A ``selection`` :class:`tables.CheckBoxColumn` wired to a row-grouping
  form.
- The ``hx-include="#filter-form"`` attribute on the table element so
  every htmx-driven sort/page request preserves the active filters.
- The ``bootstrap_htmx.html`` table template (Bootstrap 4 styling +
  pagination links that trigger htmx swaps).
- A ``partial_template_name`` hook for the body partial.

What :class:`RegionTable` adds
------------------------------
- A region-flavored ``selected-region`` checkbox name (Django form
  groups distinguish target/region/observation selections by name).
- A linkified ``name`` column whose anchor opts out of htmx
  navigation (the detail view is a full page, not a partial swap).
- A choice of which model fields appear in the list view.

The :func:`tom_targets.tables.TargetTable` is the canonical reference;
this is intentionally close to a reskin of it.
"""

from __future__ import annotations

import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html

from tom_common.htmx_table import HTMXTable
from tom_regions.models import Region, RegionList


class RegionTable(HTMXTable):
    """List-view table for :class:`tom_regions.models.Region`.

    Most of the HTMX wiring lives in :class:`HTMXTable`. This subclass
    only changes the things that need a region-specific identity: the
    selection checkbox name, the ``name`` column linkification, and
    the column set. Everything else (sort headers, pagination links,
    filter-preserving GET parameters) is inherited.

    Why ``hx-boost: false`` on the name link?
        :class:`HTMXTable` sets ``hx-boost: true`` on the table itself
        so navigation between sort/filter states stays htmx-driven.
        That default would also intercept the per-row "go to detail"
        click and load the detail page as an htmx fragment, which
        would render inside the table container rather than as a full
        page. Disabling boost on the link returns it to a regular
        full-page navigation.
    """

    # ``selection`` is provided by HTMXTable. We override only to give
    # it a region-specific input ``name`` so a downstream form (e.g. a
    # bulk-delete or add-to-RegionList action) can distinguish region
    # checkboxes from target or observation checkboxes that may share
    # the page in a future combined view.
    selection = tables.CheckBoxColumn(
        accessor="pk",
        orderable=False,
        attrs={
            "input": {"name": "selected-region", "form": "grouping-form"},
            "th__input": {
                "class": "header-checkbox",
                "form": "grouping-form",
                "onclick": "event.stopPropagation();",
            },
        },
    )

    # ``linkify=True`` tells django-tables2 to wrap the cell value in an
    # anchor pointing at the row's ``get_absolute_url``. We added that
    # method on :class:`tom_regions.base_models.BaseRegion`, so the link
    # routes to the region's detail view automatically.
    name = tables.Column(linkify=True, attrs={"a": {"hx-boost": "false"}})

    class Meta(HTMXTable.Meta):
        model = Region
        # The columns shown on the list page. ``area_sr`` and ``n_tiles``
        # come from the cached summary on Region, populated by
        # :func:`tom_regions.utils.recompute_region_summary` whenever
        # tiles are inserted. Showing them keeps the list useful at a
        # glance; the detail page renders the full picture.
        fields = ["selection", "name", "type", "area_sr", "n_tiles", "created"]

    # Override-point for the body-only template that htmx swaps in.
    # The full-page template (region_list.html) extends the TOM common
    # base; this partial renders only ``<tbody>`` plus pagination so a
    # filter change can update the table in place.
    partial_template_name = "tom_regions/partials/region_table_partial.html"


class RegionGroupTable(HTMXTable):
    """List-view table for :class:`tom_regions.models.RegionList`.

    Mirrors :class:`tom_targets.tables.TargetGroupTable` -- a table of
    region groupings with a name link (filters the regions list page
    by that group), a count of member regions, and a Delete column.
    No selection checkbox here: the row-level actions (filter / delete)
    are inline.
    """

    # ``linkify`` defaults the name to RegionList.get_absolute_url, but
    # we don't want to navigate to a detail view for the group -- we
    # want to filter the regions list page by it. Render an explicit
    # link to /regions/?regionlist__name=<id>.
    name = tables.Column(orderable=True, empty_values=())

    total_regions = tables.Column("Total Regions", orderable=False, empty_values=())
    id = tables.Column("Delete", orderable=False)

    def render_name(self, record):
        return format_html(
            '<a href="{}?regionlist__name={}">{}</a>',
            reverse("regions:list"),
            record.id,
            record.name,
        )

    def render_total_regions(self, record):
        return record.regions.count()

    def render_id(self, value):
        return format_html(
            '<a href="{}" title="Delete Group" class="btn btn-danger">Delete</a>',
            reverse("regions:delete-group", kwargs={"pk": value}),
        )

    class Meta(HTMXTable.Meta):
        model = RegionList
        fields = ["name", "total_regions", "created"]
        # ``selection`` would be misleading on this table -- there's no
        # bulk action on groups. Drop it explicitly.
        sequence = ("name", "total_regions", "created", "id")

    partial_template_name = "tom_regions/partials/region_group_table_partial.html"
