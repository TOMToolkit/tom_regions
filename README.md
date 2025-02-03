 
tom-regions
===========

tom-demoapp has the database structures for dealing with healpix skymaps and making queries if points are in the skymap (for a galaxy map) or the probability of being in a region of the skymap (for a nonlocalized event map).
Quick start
-----------

1. Add "tom_demoapp" to your INSTALLED_APPS setting like this::

    INSTALLED_APPS = [
        ...,
        "tom_regions",
    ]

2. Paths should be automatically included via tom_common.

3. Run ``python manage.py migrate`` to create the models.

4. Start the development server and visit your tom to see any changes.
