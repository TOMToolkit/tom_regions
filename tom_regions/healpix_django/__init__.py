"""Django ORM port of skyportal/healpix-alchemy for tom_regions.

This sub-package provides the building blocks for storing HEALPix multi-
resolution coverage maps in PostgreSQL via Django's ORM. It declares no
concrete models -- callers compose its parts into their own region models.

How to read this package
------------------------
The modules below are arranged so that, read top-to-bottom, they build up
the design from "what is HEALPix" to "how do we query it efficiently from
Django." Each module's docstring includes a *Reading order* footer with a
"previous" and "next" pointer, so you can step through them in the editor
without losing your place.

    1.  :mod:`.constants`   - the geometric constants and what ``LEVEL=29``
                              means.
    2.  :mod:`.encoding`    - the four representations (UNIQ, level/ipix,
                              int8range bounds, deepest-level point) and
                              how to convert between them. Pure Python.
    3.  :mod:`.fields`      - how those representations become Django Field
                              columns (``HealpixPointField``, ``HealpixTileField``).
    4.  :mod:`.lookups`     - which ORM lookups (``__overlap``, ``__contains``,
                              ...) are reused vs. added.
    5.  :mod:`.functions`   - SQL-side arithmetic on tiles: intersection,
                              area in steradians, union via ``range_agg``.
    6.  :mod:`.indexes`     - SP-GiST index helper for tile columns.
    7.  :mod:`.models`      - :class:`MOCMixin`, the abstract base that
                              bundles a tile column with its index.
    8.  :mod:`.checks`      - runtime guard (PostgreSQL >= 14) called from
                              the containing AppConfig's ``ready()``.

The companion paper is Singer et al. 2022, "HEALPix Alchemy: fast all-sky
spatial analysis using the PostgreSQL ORDBMS" (https://arxiv.org/abs/2112.06947).
The docstrings throughout this package aim to give a reader who hasn't seen
the paper enough intuition to follow the code; Leo's paper is the canonical
reference.

Public surface
--------------
- :class:`HealpixPointField` -- ``bigint`` column storing a deepest-level
  NESTED pixel index. Use for "a single point on the sky."
- :class:`HealpixTileField` -- ``int8range`` column storing a single MOC tile
  as a half-open interval of deepest-level pixel indices. Use as one row
  per tile, with a foreign key back to your "region" model.
- :class:`MOCMixin` -- abstract Django model bundling :class:`HealpixTileField`
  with its SP-GiST index. Subclass this for your tile table.
- :class:`HealpixSpGistIndex` -- the SP-GiST index used on tile columns.
- :class:`TileArea`, :class:`TileIntersect`, :class:`TileUnion` -- ORM
  expressions for solid-angle and set-theoretic operations on tiles.
- :func:`assert_postgres_supported` -- runtime guard called from the
  containing app's ``AppConfig.ready()``. Raises if the active database isn't
  PostgreSQL >= 14 (multirange types and ``range_agg`` are required).

These names are *lazily* loaded: importing this package alone does not pull
in Django, psycopg2, mocpy, or astropy. They only get imported when a
public symbol is first accessed. That keeps the encoding-only test module
(``tests/test_encoding.py``) usable under plain ``python -m unittest``
without a Django app registry.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Re-exported names. The actual values are loaded lazily via the
    # PEP 562 ``__getattr__`` below; this block only exists so static
    # analyzers and IDEs can resolve the symbols without forcing Django,
    # psycopg2, mocpy, or astropy to be importable at type-check time.
    from .checks import assert_postgres_supported
    from .fields import HealpixPointField, HealpixTileField
    from .functions import TileArea, TileIntersect, TileUnion
    from .indexes import HealpixSpGistIndex
    from .models import MOCMixin

__all__ = [
    "HealpixPointField",
    "HealpixTileField",
    "MOCMixin",
    "HealpixSpGistIndex",
    "TileArea",
    "TileIntersect",
    "TileUnion",
    "assert_postgres_supported",
]

# Symbol -> (submodule, attribute) for lazy resolution via PEP 562 __getattr__.
_LAZY_IMPORTS = {
    "HealpixPointField": ("fields", "HealpixPointField"),
    "HealpixTileField": ("fields", "HealpixTileField"),
    "MOCMixin": ("models", "MOCMixin"),
    "HealpixSpGistIndex": ("indexes", "HealpixSpGistIndex"),
    "TileArea": ("functions", "TileArea"),
    "TileIntersect": ("functions", "TileIntersect"),
    "TileUnion": ("functions", "TileUnion"),
    "assert_postgres_supported": ("checks", "assert_postgres_supported"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_name, attr = _LAZY_IMPORTS[name]
        module = import_module(f".{module_name}", __name__)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
