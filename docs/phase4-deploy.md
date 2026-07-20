# Phase 4 — Scaleway provisioning & deploy (design note)

> Goal: `vignemale deploy` → the app runs in prod on the EU cloud, with no ops.
> This is the 100% differentiating part (zero Encore code to copy — their
> control-plane is proprietary).

## 1. The asset: almost everything is already in place

The architecture was designed with this moment in mind. Phase 4 reinvents
nothing, it **wires up** what already exists:

- **The meta** (`collect`) is already the inventory of resources: services +
  endpoints, `SQLDatabase`, `Bucket`, `Secret`, `auth_handler`. The deploy reads
  this graph and knows what to create.
- **The provider switch**: the entire runtime is configured via env vars
  (`VIGNEMALE_SQLDB_*`, `VIGNEMALE_S3_*`, `VIGNEMALE_SECRET_*`,
  `VIGNEMALE_SERVICE_*`, `VIGNEMALE_SERVICE_SECRET`). **The deploy just has to set
  these variables** — no cloud logic in the runtime.
- **Prod-ready**: healthz (`/__vignemale/healthz`), drain + `keep_accepting`
  (the LB window already exists), multi-process (`VIGNEMALE_WORKERS`), JSON logs.
  Serverless Containers consume all of this as-is.
- **Migrations**: `db.migrate()` exists, applied at startup.

## 2. The Scaleway building blocks (verified)

| Vignemale resource | Scaleway service | Notes |
|---|---|---|
| `SQLDatabase` | Managed Database PostgreSQL | pgvector available ✓ ; 1 instance, N logical databases (API or SQL) |
| `Bucket` | Object Storage | S3-compatible → our `Bucket` already works via `VIGNEMALE_S3_*` |
| `Secret` | Secret Manager **or** the container's secret env | simplest: the Serverless Container's secret env |
| each `Service` | Serverless Container | image from the Scaleway Container Registry |
| access | IAM | API key (application) for the control-plane + runtime creds |

Programmatic access: **official Python SDK `scaleway`** (consistent with our
Python tooling), or HTTP API, or Terraform. See §6.

## 3. The three commands (in `vignemale-cli`, not the runtime)

```
vignemale build    → Dockerfile generated, image built, pushed to the registry
vignemale provision→ creates the Scaleway resources from the meta (DB, buckets, secrets, IAM)
vignemale deploy   → push image + Serverless Container per service + env vars + migrations
```

`deploy` orchestrates `build` + `provision` + the container update.

### The Dockerfile (generated)

Multi-stage: (1) Rust builder + maturin → the `vignemale` wheel; (2) `python-slim`
+ wheel + `vignemale-cli` + the app's code. Entrypoint:
`vignemale run /app` (or `gateway` in multi-container). Healthz already exposed →
Scaleway probes `/__vignemale/healthz`.

## 4. The structuring decision: mono-container vs multi-container

| | Mono-container (proposed default) | Multi-container |
|---|---|---|
| Form | all services in 1 container (`vignemale run`) | 1 container/service + 1 gateway |
| Inter-service calls | direct function (in-process) | signed svcauth HTTP (already built) |
| Cost | 1 container billed | N+1 containers |
| Scale | horizontal (instances) | per service, independent |
| For whom | the target: deploy **one agent** simply | large multi-team apps |

**Recommendation: mono-container by default, multi-container opt-in.** The target
(deploy an AI agent in 1 command) wants simplicity and minimal cost. We already
have the gateway + svcauth for the day multi-container is requested — but it's not
the default. The provider switch means it's the **same artifact**, just a
different deployment split.

## 5. State & idempotence

`deploy` must be replayable without recreating everything. Two options:
- **Tags + lookup** (robust): tag each Scaleway resource with
  `vignemale-app=<name>` + `vignemale-resource=<id>`, and find it again before
  creating. No state file to derive.
- **State file** (`.vignemale/deploy.json`): simpler, but can diverge from
  reality.

Recommendation: tags + lookup (state lives in Scaleway, the single source of
truth).

## 6. Decisions to settle

1. **Scaleway account**: real provisioning requires an IAM API key. Without an
   account, we develop the driver + a **`--dry-run`** (shows the plan, like
   `terraform plan`, testable without cloud) and then validate on a real account.
   The dry-run also has product value.
2. **SDK vs Terraform**: Python SDK `scaleway` (all in Python, consistent) vs
   Terraform (declarative, managed state, but a dependency + HCL language).
   Recommendation: Python SDK, with a **driver interface** (`provision_db`,
   `provision_bucket`, `build_push`, `deploy_service`, `set_secrets`) to prepare
   for multi-cloud (OVH) later.
3. **Mono vs multi-container by default**: see §4 (recommendation: mono).
4. **Migrations at deploy**: a `deploy` step that applies `migrate()` once (the
   CLI connects to the managed DB) before routing traffic — avoids the race
   between N instances.

## 7. Breakdown into deliverable slices

1. **`vignemale build`** — Dockerfile + local build + (registry push). The build
   part is testable without an account (verify that the image starts and responds
   to healthz).
2. **Scaleway driver + `provision --dry-run`** — plans the resources from the
   meta, shows the plan. Testable without an account.
3. **Real `provision`** — creates DB/buckets/secrets/IAM via the SDK. Account
   required.
4. **`deploy`** — push image + Serverless Container(s) + env vars + migrations.
   Account required.
5. **Idempotence (tags+lookup) + basic rollback + `vignemale logs/status`.**

Order: 1 and 2 first (no account, they de-risk the architecture), then 3-4 as
soon as a Scaleway account is available.

## 8. Scaleway tooling — do NOT reinvent the wheel (analyzed on June 15, 2026)

Analysis of Scaleway's GitHub (github.com/scaleway). Decision: `apply` is **thin
glue on top of the official Python SDK `scaleway`** (PyPI `scaleway`, v2.11,
Apache-2.0, ~beta-stable). It covers ALL our products, and our engine is already
in Python (reusable as-is by a Python control plane).

| Vignemale resource | SDK module | Key methods (apply + idempotence + progress) |
|---|---|---|
| Managed DB instance | `rdb.v1` `RdbV1API` | `create_instance` / `wait_for_instance` / `list_instances` (tag lookup) / `get_instance_certificate` / `get_instance_metrics` (observ.) |
| logical database | `rdb.v1` | `create_database` / `list_databases` / `create_user` / `list_privileges` |
| bucket | **no module** → S3 | Object Storage = pure S3: we **reuse our Rust code `aws-sdk-s3`** (`bucket_op`), no Scaleway SDK |
| secret | `secret.v1beta1` `SecretV1Beta1API` | `create_secret` / `create_secret_version` / `access_secret_version` / `list_secrets` |
| Serverless Container | `container.v1beta1` `ContainerV1Beta1API` | `create_namespace` / `create_container` / `update_container` / `deploy_container` / `wait_for_container` / `list_containers` (lookup) / `create_domain` |
| Container Registry | `registry.v1` | namespace to push the app image |
| IAM / creds | `iam.v1alpha1` | delegated access keys from the customer |
| observability | `cockpit` | aggregated logs/metrics (selling point) |

**SDK vs Terraform**: we keep the SDK (not Terraform/Crossplane). The SDK maps
1-to-1 onto our `Action`s, gives `wait_for_*` (→ progress stream for the deploy
log) and `list_*` (→ idempotence by tags, our design). Terraform would add HCL
generation + a binary + a state backend to manage = reinventing the control
plane's work in another tool.

**Orchestration pattern** (taken from their `serverless-api-framework-python`,
which targets Functions but shows the right sequence): *get-or-create namespace →
idempotent create/update (lookup) → deploy → wait → cleanup of the stale*.

**Consequence**: `ScalewayProvider.existing()` = `list_*` filtered by tags;
`apply()` = the `create_*`/`deploy_*` above; the control plane can be in
**Python** to reuse `vignemale-deploy` + `scaleway` directly.

### Useful Scaleway repos
- Python SDK `scaleway` (our apply dependency): github.com/scaleway/scaleway-sdk-python
- `scw` CLI (Go, shell fallback for `--local`): github.com/scaleway/scaleway-cli
- `serverless-api-framework-python` (deploy flow reference): github.com/scaleway/serverless-api-framework-python
- Terraform provider (ruled out, but a mapping ref.): github.com/scaleway/terraform-provider-scaleway

## Sources
- Serverless Containers: https://www.scaleway.com/en/developers/api/serverless-containers
- Deploy container (API): https://www.scaleway.com/en/docs/serverless-containers/api-cli/deploy-container-api/
- Managed Database PostgreSQL: https://www.scaleway.com/en/developers/api/managed-database-postgre-mysql
- Python SDK: https://github.com/scaleway/scaleway-sdk-python
</content>
