#!/usr/bin/env python
"""Fail if model changes are missing a migration (tom_app_template convention).

``makemigrations --check`` exits non-zero when the models and the committed
migrations disagree; CI runs this as its own job (see
``.github/workflows/run-tests.yml``). Note it still needs a live PostgreSQL:
``apps.ready()`` opens a connection for the postgres-version check.
"""

from __future__ import annotations

from django.core.management import call_command

from tom_regions.tests.boot_django import APP_NAME, boot_django


def main() -> None:
    boot_django()
    print(f"checking migrations for {APP_NAME}")
    # ``makemigrations`` takes app *labels*, and TomRegionsConfig sets
    # ``label = 'regions'`` (not the module name ``tom_regions``).
    call_command("makemigrations", "regions", "--check")


if __name__ == "__main__":
    main()
