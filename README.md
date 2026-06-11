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
| `api` | serveur HTTP : `@api` **typé Pydantic** (validation → **422**), `serve`, **streaming SSE**, `HTTPError` | `vignemale.api`, `examples/typed.py` |
| `Service` | regrouper les endpoints (un module = un service) → apps **multi-services** | `examples/shop/` |
| `collect` | **extraction STATIQUE** (griffe, sans exécuter l'app) → **vrai `meta.proto`** multi-service (protojson), diffable en PR | `python -m vignemale.collect <chemin>` |
| **CLI** | `vignemale run` (découvre + sert) · `vignemale check` (meta statique) | `vignemale --help` |
| `names`, `proccfg`, `runtime_config` | newtypes, process allocation, vue métriques | (internes) |

> Prochaines briques : clients typés (OpenAPI) · CLI `vignemale run/check` · `sqldb` (Postgres). Cf. `../DEV-PLAN.md`.

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
