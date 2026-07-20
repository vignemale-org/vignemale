"""Service `catalog` — one FOLDER = one service (Encore-style).

The `Service` is declared here; the endpoints live in the folder's
modules (`items.py`, …).
"""

from vignemale import Service

catalog = Service("catalog")
