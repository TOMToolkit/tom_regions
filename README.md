# tom_regions

`tom_regions` is an INSTALLED_APP for TOMToolkit that makes **Region**
a first-class model (like Target or Observation).
Regions are sky areas backed by [HEALPix](https://arxiv.org/abs/astro-ph/9905275)
multi-order coverage maps (MOCs).

Regions can be created interactively in Aladin or by uploading IVOA MOC FITS
files. In a comming refactor, `tom_nonlocalizedevents` will create regions
upon ingesting alerts containing HEALPix data.

## Acknowledgements

The `healpix_django` sub-package is a port (from the SQLAlchemy ORM to the Django ORM)
of Leo Singer's [HEALPix Alchemy](https://github.com/skyportal/healpix-alchemy) package,
which can be cited as follows:

(paper):
- Singer, L. P., Parazin, B., Coughlin, M. W., et al. (2022). "HEALPix Alchemy: Fast All-Sky Geometry and Image Arithmetic in a Relational Database for Multimessenger Astronomy Brokers." *Astronomical Journal* **163** 209. https://doi.org/10.3847/1538-3881/ac5ab8

and (code):
 - Singer, L. P., Parazin, B., Coughlin, M. W., et al. (2022). "skyportal/healpix-alchemy: Version 1.0.1." https://doi.org/10.5281/zenodo.5768564

## Requirements

- Python ≥ 3.12
- PostgreSQL ≥ 14 — `int8range`, `int8multirange`, `range_agg`, and
  SP-GiST are all required by the HEALPix tile schema. Other
  database backends are not supported; the app raises
  `ImproperlyConfigured` at startup if the configured database
  backend is not PostgreSQL ≥ 14.

## Installation

### 1. Install the app

Add `tom-regions` to your TOM's dependencies (PyPI name is
`tom-regions` with a hyphen; the Python package is `tom_regions` with
an underscore):

```bash
pip install tom-regions
```

In your TOM's `settings.py`, add both `django.contrib.postgres` and
`tom_regions` to `INSTALLED_APPS`:

```python
INSTALLED_APPS = TOMTOOKIT_INSTALLED_APPS + [
    # ... your other apps ...
    'django.contrib.postgres',
    'tom_regions',
]
```

`django.contrib.postgres` provides the `BigIntegerRangeField` and
`SpGistIndex` classes that `tom_regions` builds on. `tom_regions`
contributes a "Regions" dropdown to the navbar and an HTMX-driven
list view at `/regions/`.

### 2. Configure the database

Point your TOM's `DATABASES` setting at a PostgreSQL ≥ 14 instance:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'your_database_name',
        'USER': 'your_database_user',
        'PASSWORD': 'your_database_password',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    },
}
```

Then run the migrations:

```bash
python manage.py migrate
```

This creates four tables: `regions_region`, `regions_regiontile` (with
an SP-GiST index on the `int8range` column), `regions_regionname`,
and `regions_regionlist`.

### 3. Local development with Docker Compose

For development, the easiest way to get a PostgreSQL ≥ 14 instance is
the following `docker-compose.yml` placed at your TOM project root
(next to `manage.py`):

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: tom_regions_dev
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

Useful commands:

```bash
docker compose up -d         # start postgres in the background
docker compose down          # stop and remove the container
docker compose down -v       # also wipe the database volume
```

The credentials in the compose file match the example `DATABASES`
block above (with the database name set to `tom_regions_dev`). For
a setup that you don't want checked into version control, put your
local `DATABASES` block in a `local_settings.py` at your TOM project
root — TOMToolkit's generated `settings.py` imports it at the bottom
via `from local_settings import *`, so anything assigned there
overrides the corresponding setting:

```python
# local_settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'tom_regions_dev',
        'USER': 'postgres',
        'PASSWORD': 'postgres',
        'HOST': '127.0.0.1',
        'PORT': '5432',
    },
}
```

With the database running and migrations applied, start the dev
server:

```bash
python manage.py runserver
```
