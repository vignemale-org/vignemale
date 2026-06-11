# Vignemale

**Déploie tes agents IA en production sur ton cloud Scaleway, depuis Python, sans ops.**

> Infrastructure-from-Code (façon Encore) : cœur **Rust** + binding/SDK **Python**.
> Vision produit → [`../anvil/VISION.md`](../anvil/VISION.md) · Plan de portage → [`../DEV-PLAN.md`](../DEV-PLAN.md)

---

## 🧭 Comment ça marche

```
proto/vignemale/…            ← SOURCE DE VÉRITÉ (schémas meta / infra / runtime)
        │  build.rs (prost) génère les types Rust
        ▼
runtimes/core/  (Rust)       ← le CŒUR : lit les protos, fournit les modules
        │                       config · names · secrets · objects (S3) · …
        │  PyO3 (extension `vignemale._core`)
        ▼
runtimes/python/ (binding + SDK)
        ▼
   import vignemale          ← tu appelles le core depuis Python
```

Un appel `vignemale.X()` traverse : **SDK Python → extension `_core` (PyO3) → module Rust du core → (types proto / aws-sdk / …)**. On développe le core **et** son binding ensemble, en tranches testables.

## ✅ État actuel (modules portés depuis Encore, rebrandés)

| Module core | Rôle | Testable depuis Python |
|---|---|---|
| `config` | charge la `RuntimeConfig` (env, base64/gzip, fichier) | `load_config_from_env`, `parse_runtime_config_b64` |
| `secrets` | résout un `SecretData` (env / embedded, base64/gzip, sous-clé JSON) | `resolve_env_secret`, `resolve_b64_secret`, `resolve_json_key_secret` |
| `objects` | Object Storage **S3-compatible** (Scaleway / MinIO) | `s3_roundtrip` |
| `api` | serveur HTTP : `@api` **typé Pydantic**, **query/headers/body/params** selon la signature, **streaming SSE**, **erreurs au contrat Encore** (`{code, message, details}` — `APIError`/`HTTPError`), **CORS**, healthz | `vignemale.api`, `examples/typed.py` |
| `auth` | `@auth_handler` (un par app) + `@api(..., auth=True)` — l'auth est jouée **par le core** avant le handler (vrai 401 même en streaming) ; données d'auth dans le paramètre `auth` | `examples/secure.py` |
| `clients` / `call` | **appels service-à-service** style Encore (`from vignemale.clients import catalog` → `catalog.get_item(id=7)`) : directs en local, **HTTP signé (HMAC)** une fois déployé — même code ; propagation de l'**auth** et du **trace-id W3C** | `examples/shop/`, `tests/test_call.py` |
| `sqldb` | **Postgres** : pool (taille/timeouts configurables), **transactions** (`with db.transaction() as tx:` — rollback auto sur exception), **TLS** (CA custom via `VIGNEMALE_SQLDB_CA_CERT` — Managed DB Scaleway), chaque requête tracée (durée), types riches (**NUMERIC** sans perte, BYTEA, arrays, time/date/uuid) | `SQLDatabase`, `examples/todo.py` |
| `datamodel` | **tables Pydantic** : CRUD typé sans SQL, schéma auto (création + migration additive), champs personnels tagués `PII(purpose=…)` | `Table`/`PII`, `examples/copilote/` |
| `rgpd` | **RGPD outillé** (le différenciateur) : `vignemale rgpd map` (carte des données), `export --subject` (droit d'accès), `forget --subject` (oubli — delete ou anonymize par table) — multi-services | `vignemale rgpd map examples/copilote` |
| `observability` | **logs JSON structurés** (par requête : statut, durée, **request-id**), erreurs avec traceback corrélé, `vignemale.log` côté Python | `tests/test_observability.py` |
| `Service` | regrouper les endpoints → apps **multi-services** ; un service = un **fichier** (`catalog.py`) ou un **dossier** (`catalog/` : `__init__.py` déclare le `Service`, endpoints dans ses modules — façon Encore) | `examples/shop/` |
| `collect` | **extraction STATIQUE** (griffe, sans exécuter l'app) → **vrai `meta.proto`** multi-service (protojson), diffable en PR | `python -m vignemale.collect <chemin>` |
| **CLI** | `vignemale run` (découvre + sert) · `vignemale check` (meta statique) | `vignemale --help` |
| `names`, `proccfg`, `runtime_config` | newtypes, process allocation, vue métriques | (internes) |

> Prochaines briques : `queue` (pubsub) · clients typés (OpenAPI) · provisioning Scaleway. Cf. `../DEV-PLAN.md`.

## 📂 Structure

```
vignemale/
├── Cargo.toml                     workspace (members = core + python ; default = core)
├── proto/vignemale/…              schémas .proto (dérivés d'Encore — cf. proto/ATTRIBUTION.md)
└── runtimes/
    ├── core/                      cœur Rust
    │   ├── build.rs               prost_build → types Rust
    │   └── src/                   config.rs · names.rs · secrets/ · objects.rs · …
    └── python/                    binding PyO3 + SDK
        ├── src/lib.rs             expose le core (module `_core`)
        ├── python/vignemale/      le package SDK (__init__.py)
        └── tests/smoke.py         smoke test de bout en bout
```

## 🔧 Prérequis

- **Rust** (cargo) — https://rustup.rs
- **protoc** — `brew install protobuf`
- **uv** (venv + maturin) — `brew install uv`
- **Docker** — uniquement pour le test Object Storage (MinIO)

## ▶️ Build & test en local

```bash
cd vignemale
export PROTOC="$(which protoc)"

# 1) compiler le cœur Rust seul (rapide)
cargo build

# 2) compiler le binding Python + SDK (dans un venv)
cd runtimes/python
uv venv .venv && source .venv/bin/activate && uv pip install maturin   # 1re fois seulement
source .venv/bin/activate
maturin develop

# 3) lancer les tests
uv pip install pytest        # 1re fois seulement
python -m pytest tests/      # core (config/secrets) · API (unary/SSE/422/404) · CLI · collect (golden)

# 4) la CLI
vignemale check ../../examples/shop     # graphe meta (statique, multi-service)
vignemale run   ../../examples/shop     # découvre les services + sert
```

### Écrire & lancer une app

```python
# app.py
from vignemale.api import api, serve

@api(method="GET", path="/hello")
def hello():
    return {"msg": "bonjour"}

@api(method="GET", path="/stream", stream=True)   # streaming (agent IA)
def gen(stream):
    for tok in ["sa", "lut", " !"]:
        stream.write(tok)

serve("127.0.0.1:8080")
```
```bash
python app.py &
curl -N http://127.0.0.1:8080/stream     # voit les tokens arriver au fil de l'eau
```

Un handler reçoit ce que sa **signature** déclare : paramètres de chemin
(`/notes/:id` → `id`), `body` (JSON / modèle Pydantic), `query` (dict) et
`headers` (dict). Les **erreurs** suivent le contrat Encore — corps
`{code, message, details}` avec des codes gRPC-style mappés sur les statuts :

```python
raise APIError.not_found("note introuvable")          # → 404 {"code":"not_found",…}
raise APIError("permission_denied", "admin requis")   # → 403
# validation Pydantic ratée  → 400 invalid_argument (détail Pydantic dans details)
# JSON malformé / body requis manquant → 400 · route inconnue → 404 structuré
# exception non gérée → 500 {"code":"internal","details":{"request_id":…}}
```

CORS est ouvert par défaut (dev) ; `VIGNEMALE_CORS_ALLOW_ORIGINS=https://app.example.com`
restreint. Health check : `GET /__vignemale/healthz`.

**Prod-ready, sans configuration** :
- **arrêt gracieux** sur Ctrl-C / SIGTERM : healthz passe à 503 `shutting_down`,
  plus aucune connexion acceptée, les requêtes en vol **terminent** (borné par
  `VIGNEMALE_SHUTDOWN_TIMEOUT`, 10 s) — redéploiement sans requête coupée ;
- **timeout** par endpoint (`@api(timeout=5)`) ou global
  (`VIGNEMALE_REQUEST_TIMEOUT`, 30 s) → 504 `deadline_exceeded`, le handler
  finit en arrière-plan et ses logs sont conservés ;
- **limite de body** par endpoint (`@api(body_limit=1024)`) ou globale
  (`VIGNEMALE_MAX_BODY`, 10 Mio) → 413 `resource_exhausted`.

**Authentification** (façon Encore — l'orchestration est dans le core Rust) :

```python
@auth_handler                       # UN par app
def check(token):                   # Authorization: Bearer …, ou ?token=
    user = verify(token)
    return {"user_id": user.id} if user else None   # None → 401

@api(method="GET", path="/me", auth=True)
def me(auth):                       # reçoit les données d'auth s'il les déclare
    return {"you": auth["user_id"]}
```

Le core authentifie AVANT d'appeler le handler — y compris pour les streams
(vrai 401 avant d'ouvrir le flux SSE). Un endpoint `auth=True` sans
`@auth_handler` déclaré refuse de démarrer. `vignemale check` expose
`authHandler` et `accessType: AUTH` dans le meta.

**Appels service-à-service** (le multi-service devient réel) — on importe le
service et on l'appelle, façon Encore :

```python
from vignemale.clients import catalog

@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = catalog.get_item(id=body.item_id)   # ← même code partout
    ...
```

(`vignemale.call("catalog", "get_item", id=…)` reste la primitive sous-jacente.)

Structure d'une app multi-services (un service = un dossier, comme Encore) :

```
monapp/
├── catalog/
│   ├── __init__.py     ← catalog = Service("catalog")
│   └── items.py        ← les @api du service
└── orders/
    ├── __init__.py     ← orders = Service("orders")
    └── create.py
```

(Le style « un service = un fichier » reste supporté — les deux produisent
exactement le même graphe meta.)

- **local** (`vignemale run dossier/`) : appel direct en mémoire, zéro HTTP ;
- **déployé** : `VIGNEMALE_SERVICE_CATALOG=https://…` (posé par le deploy) →
  l'appel part en HTTP sur la route interne `/__vignemale/call/…`, **signé
  HMAC-SHA256** (`VIGNEMALE_SERVICE_SECRET`, anti-rejeu ±120 s) ;
- le **trace-id W3C** (`traceparent`) traverse les services — une requête =
  un trace_id dans les logs de tous les services impliqués ;
- les **données d'auth sont propagées** (`x-vignemale-auth-data`) : un appel
  interne vers un endpoint `auth=True` est de confiance, il ne repasse pas
  par l'auth handler (façon Encore).

### Une app avec base de données, logs et erreurs (le « vrai projet »)

```python
# todo.py — cf. examples/todo.py pour la version complète
from vignemale import SQLDatabase, api, log, serve, HTTPError

db = SQLDatabase("todo")     # le code déclare la base ; l'ENV fournit le DSN

@api(method="POST", path="/todos")
def create(body: NewTodo) -> Todo:
    row = db.query_row("INSERT INTO todos (title) VALUES ($1) RETURNING *", body.title)
    log.info("todo créé", todo_id=row["id"])
    return Todo(**row)
```

```bash
vignemale run examples/todo.py
# vignemale: démarrage du Postgres local (docker)…
# vignemale: base Postgres « todo » prête (docker local)
# vignemale: 4 endpoint(s) sur http://127.0.0.1:8080
```

**Zéro configuration** : `run` lit les ressources déclarées (statiquement, via
`collect`) et provisionne le local AVANT d'importer l'app — un Postgres Docker
partagé (`vignemale-postgres`, volume persistant), une database par
`SQLDatabase("x")`, le DSN posé dans l'env. Si `VIGNEMALE_SQLDB_<NOM>` (ou
`VIGNEMALE_SQLDB`) est déjà posé, il a priorité : même code, autre backend —
c'est le provider switch. `vignemale check` liste aussi les bases déclarées
(`sqlDatabases` dans le meta).

**Observabilité incluse, sans rien configurer** :
- chaque réponse porte un header `x-vignemale-request-id` ;
- chaque requête produit une ligne de log JSON sur stderr (endpoint, statut, `duration_ms`, `request_id`) ;
- une exception non gérée → **500 propre** `{"error": "internal error", "request_id": …}` + le **traceback complet loggé** avec le même `request_id` ;
- `vignemale.log` (`log.info("msg", champ=valeur)`) écrit au même format JSON que le core Rust ;
- niveau via `VIGNEMALE_LOG` (`debug` | `info` | `warn` | `error`, défaut `info`).

### Modèles de données & RGPD (le différenciateur vs Encore)

```python
from vignemale.datamodel import Table, PII

class User(Table):
    __database__ = "users"          # la SQLDatabase qui héberge la table
    __subject__ = "id"              # colonne qui identifie LA PERSONNE
    id: Optional[int] = None        # BIGSERIAL PRIMARY KEY auto
    email: str = PII(purpose="compte et contact")
    plan: str = "free"

user = User.create(email="ada@ex.com")     # table créée automatiquement
user = User.find_one(email="ada@ex.com")   # CRUD typé — zéro SQL
user.plan = "pro"; user.save()
```

Le schéma EST le code : table créée au premier usage, colonnes ajoutées au
modèle ajoutées à la table (migration additive), le SQL brut reste
l'échappatoire pour les requêtes complexes. Et comme le schéma déclare ses
données personnelles, **le RGPD devient outillé** :

```bash
vignemale rgpd map    monapp/                 # carte des données (pour le DPO)
vignemale rgpd export monapp/ --subject 42    # tout ce qu'on a sur la personne 42
vignemale rgpd forget monapp/ --subject 42    # droit à l'oubli, multi-services
#   par table : __on_forget__ = "delete" (la ligne saute)
#               ou "anonymize" (PII caviardés, la ligne reste pour les stats)
```

> ⚠️ Des **preuves et des mécanismes**, pas une garantie de conformité —
> l'humain (DPO/juriste) valide. Cf. `anvil/VISION.md`.

### Inclure les tests Postgres (sqldb)

```bash
docker run -d --name vignemale-pg -p 5433:5432 -e POSTGRES_PASSWORD=vignemale postgres:16
VIGNEMALE_TEST_PG=postgres://postgres:vignemale@127.0.0.1:5433/postgres python -m pytest tests/ -k sqldb
```

### Inclure le test Object Storage (S3 via MinIO)

```bash
docker run -d --name vignemale-minio -p 9100:9000 minio/minio server /data
VIGNEMALE_TEST_S3=http://127.0.0.1:9100 python -m pytest tests/ -k s3
docker rm -f vignemale-minio
```

## 🔁 Boucle de dev

Après toute modif du core ou du binding : `maturin develop` (depuis `runtimes/python`, venv actif, `PROTOC` exporté) puis relance `python -m pytest tests/`. Les tests Rust du core : `cargo test` (à la racine). La CI (`.github/workflows/ci.yml`) rejoue les deux.

> ⚠️ `PROTOC` doit être exporté à chaque build (le `build.rs` du core en a besoin).

## ⚖️ Licence

Le code du cœur et les schémas sont **dérivés d'Encore** (MPL-2.0) — voir `proto/ATTRIBUTION.md`. Attribution obligatoire.
