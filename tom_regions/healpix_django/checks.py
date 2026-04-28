"""Runtime guard rails for tom_regions.healpix_django.

The HEALPix-on-PostgreSQL design depends on three database features that
ship in PostgreSQL 14: ``int8range``, ``int8multirange``, and the
``range_agg`` aggregate. Older PostgreSQL versions and other database
backends (SQLite, MySQL, Oracle) cannot satisfy the schema, and silently
falling back to slower Python-side query paths would surprise users.

We therefore fail loudly at app-startup time. The containing AppConfig's
``ready()`` method calls :func:`assert_postgres_supported`, which raises
:class:`django.core.exceptions.ImproperlyConfigured` when the configured
default database is not PostgreSQL >= 14.

Reading order
-------------
- Previous: :mod:`tom_regions.healpix_django.models` (the abstract base
            for tile tables, whose queries depend on the features
            asserted here).
- Next:     End of the package tour. From here, return to
            :mod:`tom_regions.healpix_django` for the public-symbol
            summary, or jump out to :mod:`tom_regions.models` to see how
            the region/region-tile tables consume the building blocks.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import connection

MIN_POSTGRES_VERSION = 140000  # Django reports server_version as 14_00_00


def assert_postgres_supported() -> None:
    """Raise if the default database isn't PostgreSQL >= 14.

    Raises:
        ImproperlyConfigured: With a message pointing at the README's
            PostgreSQL setup section. Caller is responsible for catching
            (or, more typically, letting it propagate during ``manage.py``
            startup).
    """
    if connection.vendor != "postgresql":
        raise ImproperlyConfigured(
            "tom_regions requires PostgreSQL >= 14, but the configured "
            f"database backend is {connection.vendor!r}. The healpix_django "
            "sub-package uses int8range, int8multirange, and range_agg, "
            "which are only available in PostgreSQL 14+. See the tom_regions "
            "README for setup instructions."
        )
    server_version = getattr(connection, "pg_version", None)
    if server_version is None:
        # Older Django versions or unusual wrappers may not expose pg_version
        # before the first query. Defer the check rather than blocking startup.
        return
    if server_version < MIN_POSTGRES_VERSION:
        raise ImproperlyConfigured(
            "tom_regions requires PostgreSQL >= 14. "
            f"Server reports version {server_version}; need >= {MIN_POSTGRES_VERSION}."
        )
