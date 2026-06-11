"""Service `catalog` — un DOSSIER = un service (façon Encore).

Le `Service` est déclaré ici ; les endpoints vivent dans les modules du
dossier (`items.py`, …).
"""

from vignemale import Service

catalog = Service("catalog")
