"""Domain ("astronomy logic") service layer for tom_regions.

This sub-package holds the operations that combine HEALPix geometry with the
Django ORM but are *not themselves* Django plumbing -- the kind of logic a view,
a DRF serializer, a management command, or another app
(``tom_nonlocalizedevents``) all want to call. Keeping it separate from the
framework modules (``filters.py``, ``views.py``, ``forms.py``, ``tables.py``) is
a deliberate "separate the astronomy logic from the Django logic" choice: the
framework modules wire HTTP/forms/templates, and they call *into* here.

Modules
-------
- :mod:`tom_regions.services.queries` -- read selectors: which targets /
  observations fall inside a region.
- :mod:`tom_regions.services.skymap` -- probability-skymap operations: ingest a
  multi-order LIGO/Virgo skymap, derive credible-region contours, and score a
  point's enclosed credible probability.

Layering, from most framework-free to most ORM-coupled:

    healpix_django/   pure HEALPix math (no Django, no DB)
    utils.py          thin MOC <-> RegionTile row helpers
    services/         this package: astronomy operations over the ORM
    filters/views/... Django request/response plumbing

Submodules are imported explicitly (this ``__init__`` pulls in nothing heavy),
so ``import tom_regions.services`` does not drag in astropy or mocpy.
"""

from __future__ import annotations
