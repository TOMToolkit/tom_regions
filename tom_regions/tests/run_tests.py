"""Standalone test runner for tom_regions.

Materializes a minimal Django configuration via :func:`boot_django`,
then invokes ``manage.py test`` against the package. The CI workflow at
``.github/workflows/run-tests.yml`` runs::

    poetry install --with test
    python tom_regions/tests/run_tests.py

Test discovery
--------------
``call_command('test', 'tom_regions', ...)`` walks every submodule of
the package looking for ``unittest.TestCase`` subclasses, so this one
invocation picks up the existing tests under
:mod:`tom_regions.healpix_django.tests` along with any new
:mod:`tom_regions.tests.*` modules we add later. There is no need to
register test modules anywhere.

The ``--exclude-tag=canary`` flag mirrors the convention TOMToolkit
uses elsewhere: tests decorated with ``@tag('canary')`` exercise live
external services and are run via a separate runner
(:mod:`tom_regions.tests.run_canary_tests`, when we add it). The
default suite stays hermetic.
"""

from __future__ import annotations

from django.core.management import call_command

from tom_regions.tests.boot_django import APP_NAME, boot_django


def main() -> None:
    boot_django()
    print(f"running tests for {APP_NAME}")
    call_command("test", APP_NAME, "--exclude-tag=canary", verbosity=2)


if __name__ == "__main__":
    main()
