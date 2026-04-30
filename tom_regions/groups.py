"""Helpers for adding / removing / moving Regions in and out of RegionLists.

A RegionList is the tom_regions analog of tom_targets' TargetList -- a
named collection of Region objects via an M2M. The list-page form
gives the user three actions (Add / Move / Remove) plus a "select all"
shortcut; this module is the per-action implementation.

The functions here are simpler than their tom_targets counterparts in
one respect: tom_regions doesn't ship with django-guardian in v1, so
none of the guardian ``has_perm`` checks appear. If permissions land
in a future phase, this is the natural place to gate each operation.

Each function emits Django messages (success / warning / error) so the
list view's flash area surfaces what happened to the user. We don't
return anything: the caller redirects back to the list view, which
re-renders with the new flash messages.
"""

from __future__ import annotations

from django.contrib import messages

from tom_regions.models import Region, RegionList


def _resolve(region_ids):
    """Yield (region_id, region_or_None) for each id in the input list.

    Lookup failures are surfaced as ``None`` so the caller can keep
    a per-id failure list rather than aborting on the first miss.
    """
    for region_id in region_ids:
        try:
            yield region_id, Region.objects.get(pk=region_id)
        except Region.DoesNotExist:
            yield region_id, None


def add_selected_to_grouping(region_ids, grouping: RegionList, request) -> None:
    """Add each named Region to the grouping; skip already-members.

    Args:
        region_ids: list of Region pks (strings or ints).
        grouping: the RegionList to add to.
        request: HTTPRequest, used for the messages framework.
    """
    success, warnings, missing = [], [], []
    for region_id, region in _resolve(region_ids):
        if region is None:
            missing.append(str(region_id))
            continue
        if region in grouping.regions.all():
            warnings.append(region.name)
            continue
        grouping.regions.add(region)
        success.append(region.name)
    _flash(request, grouping.name, "added", success, warnings, missing,
           already_message="already in this group")


def remove_selected_from_grouping(region_ids, grouping: RegionList, request) -> None:
    """Remove each named Region from the grouping; skip non-members."""
    success, warnings, missing = [], [], []
    for region_id, region in _resolve(region_ids):
        if region is None:
            missing.append(str(region_id))
            continue
        if region not in grouping.regions.all():
            warnings.append(region.name)
            continue
        grouping.regions.remove(region)
        success.append(region.name)
    _flash(request, grouping.name, "removed", success, warnings, missing,
           already_message="not in this group")


def move_selected_to_grouping(region_ids, grouping: RegionList, request) -> None:
    """Remove each Region from any other grouping it's in, then add to ``grouping``.

    "Move" is the natural operation when a user wants a region in
    *exactly one* grouping. Membership in other groupings is dropped
    silently; the messages summarize the net effect.
    """
    success, warnings, missing = [], [], []
    for region_id, region in _resolve(region_ids):
        if region is None:
            missing.append(str(region_id))
            continue
        # Detach from every other grouping first.
        for other in region.region_lists.exclude(pk=grouping.pk):
            other.regions.remove(region)
        if region in grouping.regions.all():
            # Already in the destination; "move" is effectively a no-op.
            warnings.append(region.name)
        else:
            grouping.regions.add(region)
            success.append(region.name)
    _flash(request, grouping.name, "moved into", success, warnings, missing,
           already_message="already in destination")


def _flash(
    request,
    grouping_name: str,
    verb: str,
    success: list[str],
    warnings: list[str],
    missing: list[str],
    already_message: str,
) -> None:
    """Common message-emitter for the three grouping actions."""
    if success:
        messages.success(
            request,
            f"{verb.capitalize()} {len(success)} region"
            f"{'' if len(success) == 1 else 's'}: "
            f"{', '.join(success)}",
        )
    if warnings:
        messages.warning(
            request,
            f"Skipped {len(warnings)} region"
            f"{'' if len(warnings) == 1 else 's'} ({already_message}): "
            f"{', '.join(warnings)}",
        )
    if missing:
        messages.error(
            request,
            f"Could not find region id(s): {', '.join(missing)}",
        )
