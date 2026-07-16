"""Django admin registrations for tom_regions.

Phase 1 ships minimal admin support: the four region tables show up in
the admin index and can be browsed. Tile creation through the admin is
not the intended workflow (use the form/serializer paths in Phase 3),
so RegionTile is read-only here -- the inline on RegionAdmin is
collapsed and capped, since a single skymap can have hundreds of
thousands of tiles and rendering all of them in admin would freeze the
page.
"""

from __future__ import annotations

from django.contrib import admin

from tom_regions.models import Region, RegionList, RegionName, RegionTile


class RegionTileInline(admin.TabularInline):
    model = RegionTile
    extra = 0
    max_num = 50  # admin should never try to render more than this
    classes = ["collapse"]
    readonly_fields = ("hpx", "probdensity")
    can_delete = False


class RegionNameInline(admin.TabularInline):
    model = RegionName
    extra = 0


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "creator", "n_tiles", "area_sr", "created")
    list_filter = ("type",)
    search_fields = ("name", "description")
    raw_id_fields = ("creator",)
    readonly_fields = ("created", "modified", "n_tiles", "area_sr", "max_depth")
    inlines = [RegionNameInline, RegionTileInline]


@admin.register(RegionTile)
class RegionTileAdmin(admin.ModelAdmin):
    list_display = ("id", "region", "hpx", "probdensity")
    list_filter = ("region",)
    raw_id_fields = ("region",)
    readonly_fields = ("hpx",)


@admin.register(RegionName)
class RegionNameAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "created")
    raw_id_fields = ("region",)
    search_fields = ("name",)


@admin.register(RegionList)
class RegionListAdmin(admin.ModelAdmin):
    list_display = ("name", "created")
    search_fields = ("name",)
    filter_horizontal = ("regions",)
