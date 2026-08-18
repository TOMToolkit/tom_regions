"""Management command: rebuild the ``TargetHealpix`` cache from the Target table.

The Target ``post_save`` signal keeps :class:`~tom_regions.models.TargetHealpix`
current for interactive edits, but bulk operations (``bulk_create``,
``bulk_update``, ``QuerySet.update``, raw SQL loads) bypass signals. Run this
after such a load -- or once when first installing tom_regions into a TOM that
already has targets -- to make the cache authoritative::

    python manage.py backfill_target_healpix

Deletions are handled by the ``post_delete`` signal, so this command only needs
to (re)create and refresh rows; pass ``--prune`` to also drop cache rows whose
target no longer qualifies (useful if targets were bulk-deleted).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Populate/refresh the TargetHealpix point cache for all sidereal targets."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Also delete cache rows whose target no longer has coordinates "
            "(e.g. after a bulk delete). Off by default to keep the run cheap.",
        )

    def handle(self, *args, **options) -> None:
        from astropy.coordinates import SkyCoord

        from tom_regions.healpix_django.encoding import skycoord_to_point
        from tom_regions.models import TargetHealpix
        from tom_targets.models import Target

        created = updated = skipped = 0
        seen_ids: list[int] = []
        # ``iterator()`` keeps the working set small for large target tables.
        for target in Target.objects.all().iterator():
            if target.ra is None or target.dec is None:
                skipped += 1
                continue
            point = skycoord_to_point(SkyCoord(target.ra, target.dec, unit="deg"))
            _, was_created = TargetHealpix.objects.update_or_create(
                target_id=target.id, defaults={"hpx": point}
            )
            created += int(was_created)
            updated += int(not was_created)
            if options["prune"]:
                seen_ids.append(target.id)

        message = (
            f"TargetHealpix backfill complete: {created} created, {updated} updated, "
            f"{skipped} skipped (no coordinates)."
        )
        if options["prune"]:
            pruned = TargetHealpix.objects.exclude(target_id__in=seen_ids).delete()[0]
            message += f" {pruned} stale rows pruned."

        self.stdout.write(self.style.SUCCESS(message))
