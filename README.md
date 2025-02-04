 
tom-regions
===========

tom-regions has the database structures for dealing with HEALPix skymaps and making queries if points are in the skymap (for a galaxy map) or the probability of being in a region of the skymap (for a nonlocalized event map).

Quick start
-----------

1. Add "tom_regions" to your INSTALLED_APPS setting like this::

    INSTALLED_APPS = [
        ...,
        "tom_regions",
    ]

2. Run ``python manage.py migrate`` to create the models.
