# Vignemale Cloud — deployment platform (design note)

> Vision: `vignemale login` + `vignemale deploy` → a team manages its
> deployments from a platform. The CLI talks to a **control plane**
> (Vignemale server) that orchestrates provisioning + deploy on the EU cloud.

## 1. The architectural shift (≠ Phase 4 "BYOC-laptop")

The sketched Phase 4 put Scaleway orchestration **in the CLI**, run from the
dev's laptop. The "Cloud / team" model **centralizes** this orchestration in the
control plane. Consequences:

- **The orchestration engine (Scaleway driver) lives on the server side**, not in
  the CLI. The CLI becomes a **thin client**: it authenticates, sends a deploy
  request, and streams the logs back.
- Benefits: centralized audit, RBAC, consistency (a single engine), **no cloud
  credentials on laptops**, centralized and encrypted secrets.
- We keep a **self-hosted / open-source** mode: the same engine runs locally via
  `vignemale deploy --local` (BYOC from the laptop), for those who don't want the
  platform.

→ Derived decision: the **Scaleway driver** (slices 2-4 of Phase 4) must be a
**reusable building block** called either by the control plane or by the CLI in
`--local` mode.

## 2. Anatomy of the control plane

| Building block | Role |
|---|---|
| **API** (REST/gRPC) | serves the CLI + the team web dashboard |
| **Identity** | users, teams/orgs, **RBAC**, tokens (login via device-flow) |
| **Model** | apps, **environments** (staging/prod), config + secrets per env |
| **Orchestrator** | **job queue** (deploy = async, multi-step, idempotent, retries) + workers that call Scaleway |
| **State** | provisioned resources (Scaleway IDs), rollout history, rollback |
| **Store** | Postgres (state) + **encryption of secrets at rest** |
| **Observability** | log/metric aggregation (the differentiating selling point) |

## 3. Structuring decisions

1. **What does `vignemale deploy` send?**
   - (a) *CLI builds+pushes the image* (we already have the fast build + base
     image), then sends `meta + image digest + config` to the server, which
     provisions+deploys. **Lightweight server. MVP recommendation.**
   - (b) *Server builds from git* (Encore-style: push = server-side build,
     reproducible). More of a "real platform" but requires a **build farm**.
   → Start with (a), keep (b) as an evolution.

2. **Where do deployments go? — DECIDED: BYOC + managed control plane that bills.**
   We **always** deploy **into the CUSTOMER's Scaleway account** (they connect a
   restricted IAM key), but orchestration goes through the **remote server** that
   keeps control (RBAC, state, secrets, audit, history). Vignemale **bills for the
   platform service** (orchestration + management + observability), not the
   compute (which the customer pays directly to Scaleway).
   - Advantage: no cloud reseller status, no shared compute infra to isolate, but
     we keep a billable product and control.
   - Security: the control plane holds the **Scaleway creds of N customers** →
     encryption at rest + per-tenant secret isolation remain critical (but no
     multi-tenant compute isolation to manage).
   - `--local`: the same engine runs on the laptop without a server (open-source /
     self-hosted), deploying into the same customer account.

3. **Login**: OAuth **device-flow** (`vignemale login` opens the browser,
   confirms a code) → token in `~/.vignemale/auth.json`. The CLI attaches the
   token to every request.

4. **Security — THE hard point**: the control plane holds the cloud creds and the
   secrets of N teams. Encryption at rest (KMS/envelope), least-privilege IAM per
   app/env, **tenant isolation**, audit log, secret rotation. This is the critical
   effort as soon as we move to managed multi-tenant.

## 4. The deploy protocol (sketch)

```
vignemale login                         # device-flow → local token
vignemale deploy --env prod             # build+push image, POST to the control plane

POST /apps/{app}/envs/{env}/deploys
  { meta, image_digest, config }  ->  { deploy_id }
GET  /deploys/{deploy_id}/logs (stream SSE)

Server steps (idempotent job, resumed on failure):
  validate meta → diff vs state → provision (missing DB/bucket/secrets) →
  migrations → rollout container (Serverless Container) → health →
  traffic switch → done   (otherwise automatic rollback)
```

The `meta` (already produced by `collect`) is the input: it tells the server which
resources exist to reconcile. The **provider switch** means that deploying =
creating the resources then **setting the `VIGNEMALE_*` variables** on the
container.

## 5. Control plane stack

API + Postgres (state) + job queue + Scaleway workers + web dashboard.
Bootstrap with a stable stack; **dogfood in Vignemale** once mature (the control
plane is itself a Vignemale app deployable on Scaleway). Watch out for the
bootstrap (chicken/egg: the first deploy is done by hand).

## 6. Phased path

1. **`vignemale login`** (device-flow) + minimal control plane (auth, apps/envs
   model) — without real deploy. Validates identity and the CLI↔server link.
2. **Scaleway orchestration engine** (the Phase 4 driver), reusable:
   `provision --dry-run` → real. Callable in `--local` mode or by the server.
3. **`vignemale deploy`**: CLI builds+pushes image → POST to the server →
   orchestration → log stream. End-to-end on a real app.
4. **Team dashboard**: RBAC, envs, secrets, history, rollback.
5. **Aggregated observability** (logs/metrics). Then evolution to **managed
   multi-tenant + billing**.

## 7. Risks / open questions

- **Managed multi-tenant** = security + billing + compliance: very heavy, not to
  be underestimated (≠ BYOC).
- **Server-side build** (option 1b) = a build farm to operate.
- **Bootstrap/dogfood** of the control plane.
- Reusability of the Scaleway driver between CLI `--local` and server: to be
  designed as a library from slice 2 of Phase 4.

## Link with the existing work
- `docs/phase4-deploy.md` remains valid for the **orchestration engine**; this
  document only decides **where it runs** (centralized server vs laptop) and
  **how the team drives it** (login + platform).
- The CI base image (`docker/runtime.Dockerfile` + workflow) serves option 1a:
  the CLI quickly produces a multi-arch image pushable to the target registry.
</content>
</invoke>
