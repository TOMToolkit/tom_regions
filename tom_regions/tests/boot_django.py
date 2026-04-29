"""Bootstrap minimal Django settings for standalone testing of tom_regions.

The TOMToolkit installed-app convention (mirroring skyportal/tom_antares)
is to ship a self-contained test entrypoint in the package itself, so
the test suite runs without depending on a host TOM. This module is the
config side of that pattern: it calls :func:`django.conf.settings.configure`
with just enough Django settings to exercise tom_regions' models, views,
and template tags.

Why we don't import a host TOM's settings
-----------------------------------------
Two reasons. First, a host TOM's settings drag in many unrelated apps
(observation facilities, alert brokers, dataproduct processors) that
take seconds to import and whose tests we don't want to run. Second,
relying on a TOM's settings means contributors must stand one up before
they can run tests -- a ten-minute friction we'd rather not impose.

The trade-off is that this module has to track tom_regions' real
dependencies: whenever we add an installed app or middleware that the
tests need, it goes here too.

PostgreSQL is required
----------------------
tom_regions hard-fails on non-postgres backends (see
:func:`tom_regions.healpix_django.checks.assert_postgres_supported`).
The DB config below reads host / port / credentials from environment
variables so CI and local docker-compose setups can target the same
boot. Defaults match the project's ``docker-compose.yml``.

Running the tests
-----------------
::

    docker compose up -d                        # if not already running
    poetry install --with dev
    python tom_regions/tests/run_tests.py
"""

from __future__ import annotations

import os

import django
from django.conf import settings


APP_NAME = "tom_regions"


def boot_django() -> None:
    """Materialize a minimal Django config and call :func:`django.setup`.

    Idempotent: calling twice is fine -- ``settings.configured`` short-
    circuits the second call. We rely on this so that a developer
    importing ``boot_django`` from a one-off REPL session can also
    ``call_command(...)`` without fuss.
    """
    if settings.configured:
        return
    settings.configure(
        DEBUG=False,
        SECRET_KEY="tom-regions-test-only-do-not-use-in-prod",
        # tom_regions hard-requires PostgreSQL >= 14. The test DB is
        # ``test_<NAME>`` (Django prepends ``test_`` automatically).
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": os.environ.get("POSTGRES_DB", "tom_regions_test"),
                "USER": os.environ.get("POSTGRES_USER", "postgres"),
                "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
                "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
                "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            },
        },
        # The minimum set required by the existing test surface. View-
        # level tests that exercise tom_common's HTMXTable mixin or DRF
        # endpoints will need ``rest_framework``, ``django_filters``,
        # ``django_tables2``, ``django_htmx``, and ``crispy_forms``
        # added here -- left out for now to keep the boot fast.
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            # ``django.contrib.postgres`` is not strictly required at
            # boot time (it has no models), but its presence makes
            # ``BigIntegerRangeField`` / ``SpGistIndex`` checks happy
            # in some Django versions.
            "django.contrib.postgres",
            APP_NAME,
        ],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        # Django's test client uses 'testserver' as the host header;
        # without it here the client gets a 400 before any view runs.
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        USE_TZ=True,
        # Required for any view that reverses a URL during a test.
        ROOT_URLCONF="tom_regions.urls",
    )
    django.setup()
