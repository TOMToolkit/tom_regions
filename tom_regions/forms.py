"""Forms for the Region create flow.

Two concrete forms cover the v1 create modes:

- :class:`RegionFromAladinForm` -- the user draws a circle / rectangle /
  polygon inside Aladin Lite's own *Overlays > MOC > From Selection*
  menu; that produces a named MOC overlay in Aladin's overlay panel.
  The list page's "Save MOC" form section harvests the chosen overlay
  via ``moc.serialize('json')`` and posts the IVOA JSON dict here for
  persistence. Aladin owns the geometry rasterization; the server
  reconstructs an in-memory ``mocpy.MOC`` via ``MOC.from_json`` and
  writes the tile rows.
- :class:`RegionMOCUploadForm` -- a FITS file upload for users bringing
  in pre-computed MOCs from external IVOA tooling (Aladin Desktop's
  "Save MOC", mocpy CLI, TOPCAT, an upcoming LIGO localization-map
  pipeline). Lives on its own create page rather than the list view.

Both forms set ``Region.type`` to the appropriate
:data:`tom_regions.base_models.REGION_TYPE_CHOICES` value before saving,
so a later filter on ``type`` can distinguish hand-drawn regions from
file-imports.

Why one form per mode (rather than one with a mode-switching field)
-------------------------------------------------------------------
The validation logic differs per mode: the Aladin form validates a
JSON dict and an existing-name uniqueness pre-check; the upload form
validates a FITS payload. Django's form-validation machinery is most
ergonomic when the visible fields match the mode exactly. The view
(:mod:`tom_regions.views`) picks the right form class per request,
so the user sees a focused form with no irrelevant inputs.
"""

from __future__ import annotations

import json
import logging

from crispy_forms.helper import FormHelper
from django import forms

from tom_regions.base_models import (
    REGION_TYPE_ALADIN,
    REGION_TYPE_FITS_MOC,
)
from tom_regions.models import Region
from tom_regions.utils import bulk_create_tiles

logger = logging.getLogger(__name__)


class _BaseRegionCreateForm(forms.ModelForm):
    """Shared metadata fields and the post-save tile-insertion hook.

    Subclasses override :meth:`build_moc` to materialize the MOC from
    their mode-specific inputs; this base class handles everything
    else (model save, tile insertion, summary recompute).

    Crispy-forms wiring
    -------------------
    Each instance gets a :class:`FormHelper` so ``{% crispy form %}``
    renders fields with the project's Bootstrap 4 template pack. Two
    helper settings are non-default:

    - ``form_tag = False`` -- the page template owns the ``<form>``.
    - ``disable_csrf = True`` -- the page template emits the CSRF
      token via ``{% csrf_token %}``.

    Sub-form-specific layout (e.g., placing fields side by side) can
    be added by overriding ``__init__`` and assigning
    ``self.helper.layout = Layout(...)``. v1 uses default layouts.
    """

    class Meta:
        model = Region
        fields = ["name", "description"]

    # Subclasses set this so ``Region.type`` reflects how the region
    # was constructed. Stored on the row for later filtering and
    # human-friendly display in the list view.
    region_type: str = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.form_show_labels = True

    def build_moc(self):
        """Construct the :class:`mocpy.MOC` for this form's input.

        Subclasses must implement this. Called after the parent
        :meth:`ModelForm.save` has persisted the Region row, so a
        ``self.cleaned_data`` lookup is safe and the Region has a
        primary key.
        """
        raise NotImplementedError

    def save(self, commit: bool = True) -> Region:
        # ``commit=False`` would leave the Region detached and
        # ``bulk_create_tiles`` would have nothing to FK at; the v1
        # contract is that create forms always commit.
        if not commit:
            raise NotImplementedError(
                "tom_regions create forms always commit; the tile insertion "
                "depends on a persisted Region row."
            )
        region = super().save(commit=False)
        region.type = self.region_type
        region.save()
        moc = self.build_moc()
        bulk_create_tiles(region, moc)
        return region


class RegionFromAladinForm(_BaseRegionCreateForm):
    """Persist a MOC drawn interactively in Aladin Lite.

    The list page's "Save MOC" form section drives this form. Its JS
    bridge enumerates Aladin's user-drawn MOC overlays, lets the user
    pick one, then on submit calls ``moc.serialize('json')`` on the
    chosen overlay and stuffs the result into ``moc_json`` (a hidden
    field). ``aladin_overlay_name`` carries Aladin's auto-name (e.g.
    ``cone``, ``poly_1``) so the server response can ask the browser
    to remove that specific overlay before adding the saved version
    back under the user's chosen name.

    The MOC reconstruction round-trips through :func:`mocpy.MOC.from_json`,
    which means we don't have to trust the geometry math of whichever
    Aladin Lite version the user has cached -- mocpy validates the
    dict shape before we write tiles.

    Why we don't preserve the original Aladin shape
    -----------------------------------------------
    Aladin can produce circular, rectangular, or polygon MOCs through
    the same selection menu. At the database layer the geometry has
    already been rasterized to HEALPix tiles -- the original shape is
    lost -- and the use cases tom_regions cares about (point-in-region,
    region-overlap, area, probability containment) all operate on the
    tile set, not on the shape. So we tag every Aladin-drawn region
    with :data:`REGION_TYPE_ALADIN` and don't try to recover or
    preserve cone-vs-rect-vs-polygon. If shape provenance ever becomes
    a feature, the Aladin overlay name (e.g., ``cone``, ``poly_1``) is
    a hint we can mine retroactively.
    """

    region_type = REGION_TYPE_ALADIN

    moc_json = forms.CharField(widget=forms.HiddenInput, required=True)
    aladin_overlay_name = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_moc_json(self) -> dict:
        raw = self.cleaned_data["moc_json"]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"moc_json is not valid JSON: {exc}")
        if not isinstance(payload, dict) or not payload:
            raise forms.ValidationError(
                "moc_json must be a non-empty IVOA dict of "
                "{'<order>': [<ipix>, ...], ...}; got "
                f"{type(payload).__name__}."
            )
        # Light shape validation; mocpy will catch deeper issues at
        # parse time. We cast keys to str (the IVOA standard) and
        # values to lists of ints to surface obvious malformations
        # with a clearer error than mocpy's.
        for order_key, ipix_list in payload.items():
            try:
                int(order_key)
            except (TypeError, ValueError):
                raise forms.ValidationError(
                    f"moc_json key {order_key!r} is not an integer order."
                )
            if not isinstance(ipix_list, list):
                raise forms.ValidationError(
                    f"moc_json[{order_key!r}] is not a list of pixel ids."
                )
        return payload

    def build_moc(self):
        from mocpy import MOC

        return MOC.from_json(self.cleaned_data["moc_json"])


class RegionMOCUploadForm(_BaseRegionCreateForm):
    """Region from a FITS-encoded MOC file upload.

    FITS is the canonical IVOA wire format for MOCs (mocpy, Aladin
    Desktop, TOPCAT, and most VO tooling speak it natively). The
    upload path lets users bring in pre-computed MOCs produced
    externally -- e.g., by intersecting a survey footprint with a
    galaxy catalog in mocpy, then saving via ``MOC.save(...)``.
    Phase 4 LIGO skymap ingest will use the same code path.
    """

    region_type = REGION_TYPE_FITS_MOC

    moc_file = forms.FileField(
        label="MOC FITS file",
        help_text="Upload an IVOA MOC FITS file (the format mocpy / Aladin / TOPCAT save).",
    )

    def build_moc(self):
        from mocpy import MOC

        # ``MOC.from_fits`` accepts a path or a file-like object; the
        # uploaded file is the latter.
        return MOC.from_fits(self.cleaned_data["moc_file"])
