#!/usr/bin/env python
"""Run only the ``@tag('canary')`` tests (tom_app_template convention).

Canary tests exercise live external services and are excluded from the
default suite (``run_tests.py`` passes ``--exclude-tag=canary``); the
scheduled ``run-canary-tests.yml`` workflow runs this module daily.
tom_regions has no canary tests yet -- the hermetic suite covers
everything -- so this is a no-op until Layer 1 adds live GraceDB fetches.
"""

from __future__ import annotations

from django.core.management import call_command

from tom_regions.tests.boot_django import APP_NAME, boot_django


def main() -> None:
    boot_django()
    print(f"running canary tests for {APP_NAME}")
    call_command("test", APP_NAME, "--tag=canary", verbosity=2)


if __name__ == "__main__":
    main()
