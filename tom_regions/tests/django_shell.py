#!/usr/bin/env python
"""Interactive Django shell against the standalone test settings.

Developer convenience from the tom_app_template: poke at tom_regions
models/services without standing up a host TOM (the docker-compose
postgres must be running; see boot_django.py). The template invokes
``shell_plus``, but that needs django-extensions, which tom_regions
doesn't depend on -- plain ``shell`` keeps the dependency surface flat.
"""

from __future__ import annotations

from django.core.management import call_command

from tom_regions.tests.boot_django import boot_django


def main() -> None:
    boot_django()
    call_command("shell")


if __name__ == "__main__":
    main()
