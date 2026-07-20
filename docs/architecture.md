# Vignemale Cloud — control plane architecture

> Status as of June 15, 2026: the deployment path is **proven in prod**
> (`vignemale deploy` → app + serverless database live on Scaleway), but driven
> from the laptop = **PoC**. This document describes the **control plane** (the
> server that turns Vignemale into a platform) and the PoC → prod path.

## 1. From PoC to product — what has to change

| Aspect | Current PoC | Target |
|---|---|---|
| Engine/server language | Python (`vignemale-deploy`) | **Go** (type-safe, `scaleway-sdk-go` as the reference SDK) |
| Orchestration | on the laptop (CLI) | **server** (control plane), CLI = thin client |
| Image build | manual on the laptop (emulated `--from-source`) | **server-side** (push the code → the platform builds + deploys) |
| State | local JSON file | **Postgres** = source of truth (apps, envs, resources, deploys) |
| Cloud creds / secrets | shell env variables | **encrypted** in the control plane, injected at deploy time |
| Migrations | loading the app locally | **job in the customer's account** (Serverless Job, Go SDK) with the app's image |
| Reconcile | best-effort `ensure_*` | **desired/actual diff, lock, rollback, retries** |
| Identity / team / billing | nonexistent | **login, orgs/RBAC, audit, metering** |
| Governance | deploy = immediate | **SYSTEMATIC devops approval** (Vignemale web panel): plan reviewed + validated before any apply; email to the dev |
| Deploy trigger | manual CLI | `vignemale deploy` **or** `git push vignemale` |

**Decision (June 15, 2026): the deployment engine and the control plane are in
Go.** The Python `vignemale-deploy` we wrote was the **PoC that de-risked the
flow in real production** (SDK calls, serverless DSN format, resource mapping,
migrations) — that knowledge transposes as-is to Go. The Go SDK covers all our
products (`serverless_sqldb`, `container`, `rdb`, `secret`, `registry`, `iam`,
`jobs`, `cockpit`, `billing`).

**The Python/Go boundary**: `collect` (extraction of the `meta`) parses Python
(griffe) → **stays in Python**, but runs **inside the build step** (which has the
source + Python anyway) and **emits the `meta` as an artifact**. The Go control
plane only consumes the `meta` (proto, already language-agnostic) + the image →
**Go never touches Python.**

## 2. Overview

```
  Dev / CI                 Vignemale Cloud control plane (Go)         CUSTOMER's Scaleway account
 ┌─────────┐  push code  ┌────────────────────────────────────┐    ┌────────────────────────┐
 │ vignemale│  + token   │  API (Go) ──► Postgres (state)       │SDK │  Serverless Container  │
 │   CLI    │───────────►│   │           + job queue SKIP LOCKED │ Go │  Serverless SQL DB     │
 │ (login,  │   SSE      │   ├─► BUILD worker (Python collect    │───►│  Object Storage        │
 │  deploy) │◄───────────│   │     + BuildKit) → image + meta    │creds│  Container Registry    │
 └─────────┘  (logs)     │   └─► DEPLOY worker (Go) ─► reconcile │deleg.  Serverless Job (migrations)
                         │  Encrypted secrets/creds · RBAC       │    └────────────────────────┘
                         └────────────────────────────────────┘
         Web admin panel (OUR SaaS product: approval + management) ─────┘
```
Two worker types: **build** (needs Python+griffe for `collect` and
BuildKit for the image; produces the image + the `meta` + the plan) and **deploy** (pure
Go: Scaleway reconciliation). The `meta` (proto) is the contract between the two.

### 2.1 The journey (the real UX)

**Developer:**
1. `uv add vignemale` — installs the lib in their app.
2. `vignemale login` — authenticates and **attaches the app to their company project**
   (org/env). Along the way, **initializes the `vignemale` git remote**.
3. `vignemale run` — develops locally (the agent runs, local infra is automatic).
4. Deploys: `vignemale deploy` **or** `git push vignemale`.

**DevOps (admin panel):**
5. Receives a **notification**: "a dev pushed to project X".
6. Sees **exactly what will be applied**: the resource diff + the
   **company-level configured parameters** (region, scaling, budget,
   secrets, quotas).
7. **Accepts or refuses.**
8. On acceptance: deployment → **email to the dev** (success + URL, or failure).

This is a **governance gate**: nothing lands on the cloud without a
responsible person having seen and validated the plan. A strong enterprise
differentiator (control + visibility + GDPR), and it directly reuses the
reconciler's **plan**.

**Model (reminder, decided)**: BYOC + a managed control plane that bills. We always
deploy **into the customer's Scaleway account** (a delegated IAM key they connect);
the control plane keeps control (state, RBAC, secrets, audit) and bills for the
**platform service**, not the compute.

## 3. The control plane in detail

### 3.1 Responsibilities
- Authenticate (users, orgs, tokens) and authorize (**RBAC**: dev / devops / admin).
- Hold the cloud credentials and application secrets, **encrypted**.
- Receive a trigger (`vignemale deploy` or `git push`), **build**,
  **compute the plan**, then submit it to the **devops approval gate**.
- After approval, execute the deploy **asynchronously, idempotently,
  resumably**, **stream** the progress, and **notify** (email to the dev).
- Be the **source of truth**: what is deployed, where, in what state, with
  what history (rollback) and **which approval decision** (audit).
- Aggregate observability; measure usage (billing).

### 3.2 Components
1. **API** (Go) — REST + SSE for logs; scoped per org/token.
2. **Postgres**: the state (see 3.4).
3. **Job queue**: `SELECT … FOR UPDATE SKIP LOCKED` on a `jobs` table
   (same primitive as the future `queue`). One deploy = one build job then one deploy job.
4. **Build worker**: Python (collect/griffe) + BuildKit → amd64 image + `meta`.
5. **Deploy worker** (Go): dequeues, executes the **reconciler** (`scaleway-sdk-go`),
   writes the progress (steps) read by the API over SSE.
6. **Secrets/creds**: envelope encryption (master key → data keys), decrypted
   *just-in-time* by the worker for container injection. Never exposed to the CLI.
7. **Admin panel** = **OUR hosted web product** (the Vignemale Cloud SaaS UI):
   this is where devops receives notifications, **reviews the plan + the company params,
   approves/refuses**, and manages apps/envs/secrets/logs/members. It is the visible face
   of the control plane (the CLI only does dev + triggering).

### 3.3 Lifecycle of a deploy

```
TRIGGER (either):
  vignemale deploy             → pushes the SOURCE to the control plane
  git push vignemale           → the "vignemale" remote (set at login) receives the push
        └─ control plane: deployments(status=queued) + enqueue BUILD job → {deploy_id}

BUILD worker (Python collect + BuildKit):
  b1. collect (griffe) → meta (proto)                      [static extraction]
  b2. docker build (native amd64) → push to customer registry → image_digest
  b3. PLAN = reconcile(meta vs state+Scaleway, merged with the COMPANY CONFIG)
            [create/update/delete diff + org params: region, scaling, budget, secrets]
  b4. status = pending_approval → NOTIFIES the admin panel (the devops)

APPROVAL GATE (human):
  the devops sees in the panel: the exact diff + the applied company parameters
  ├─ REJECTED → status=rejected, email to the dev (reason)
  └─ APPROVED → enqueue DEPLOY job

DEPLOY worker (Go):
  1. advisory lock on (env_id)         ← only one concurrent deploy per env
  2. load customer creds (decrypt) + state (resources table)
  3. APPLY resources (serverless DB, buckets, secrets)     [idempotent, IDs → resources table]
  4. MIGRATE: Serverless Job in the customer account, app image, `vignemale migrate`
  5. ROLLOUT container (new revision) → health check
  6. switch traffic → status=succeeded   (otherwise ROLLBACK to previous revision, status=failed)
  7. release lock; steps written continuously (→ panel SSE); EMAIL to the dev (success/failure + URL)
```
The **plan is computed BEFORE approval** (at the end of the build): it is what the
devops reviews. The **company config** (allowed region, scaling, budget,
secrets, quotas) is defined at the org/env level and **merged** with what the app
declares — the app expresses intent, the org sets the frame. **Approval is SYSTEMATIC**
(decided): every deploy, whatever the env, goes through `pending_approval` — no
bypass. That is the governance guarantee.

### 3.4 Data model (Postgres sketch)
```
orgs(id, name, plan)                       users(id, email)
memberships(user_id, org_id, role)         api_tokens(id, org_id, hash, scopes)  -- role: dev | devops | admin
cloud_credentials(id, org_id, provider, enc_blob, scopes)   -- customer IAM key, encrypted
apps(id, org_id, name, git_repo)                            -- git_repo: the "vignemale" remote
environments(id, app_id, name, region, db_backend)          -- systematic approval (no flag)
env_config(env_id, key, value)            -- COMPANY params: region, scaling, budget, quotas…
secrets(id, env_id, name, enc_value, version)               -- encrypted
resources(id, env_id, kind, logical_name, provider_id, meta)-- registry of Scaleway resources
deployments(id, env_id, source_ref, image_digest, meta_json, plan_json, status, created_by, created_at, finished_at, error)
   -- status: queued→building→pending_approval→(approved|rejected)→deploying→(succeeded|failed)
approvals(id, deployment_id, decided_by, decision, reason, ts)         -- the devops gate
deployment_steps(id, deployment_id, seq, name, status, message, ts)    -- progress (SSE)
notifications(id, deployment_id, kind, to, status, ts)                 -- dev email / panel alert
jobs(id, type, payload, status, attempts, run_after, locked_by, locked_at)  -- SKIP LOCKED queue
audit_log(id, org_id, actor, action, target, ts)
```
`plan_json` (the diff computed at build time) is what the devops reviews; `approvals`
records the decision; `env_config` carries the company parameters merged into the plan.
The `resources` table replaces the local JSON file: it holds the Scaleway IDs
and the few non-recoverable secrets (RDB password in managed mode; the default
serverless mode has none, IAM auth).

### 3.5 Security (the hard part)
- **Customer cloud creds**: an IAM key **scoped to the minimum** (RDB, Object
  Storage, Containers, Registry, Secret, IAM-read), encrypted at rest.
- **Envelope encryption**: master key (Scaleway Key Manager / KMS) → data keys
  per org; the worker decrypts in memory, never in logs.
- **Tenant isolation**: every request is scoped to the token's org; no shared
  multi-tenant compute to isolate (BYOC) — isolation applies to state + secrets.
- **Audit**: every sensitive action (deploy, secret read, creds rotation) is logged.
- **CLI**: holds only a short-lived token; neither cloud creds nor secrets locally.

### 3.6 Concurrency & idempotency
- **One deploy at a time per (app, env)**: Postgres advisory lock; the others
  wait or are rejected.
- **Idempotent**: every step checks the state (`resources` table + Scaleway
  lookup by tags) before acting → a resumed job never doubles anything.
- **Rollback**: on failure after the rollout, switch the container back to the
  previous revision (Scaleway keeps revisions); created resources remain (safe).

## 4. The CLI becomes a thin client
- `vignemale login`: OAuth device-flow → token in `~/.vignemale/auth.json`,
  attaches the app to the company project, and **sets the `vignemale` git remote**.
- Triggering a deploy: `vignemale deploy` (pushes the source) **or** `git push
  vignemale`. Both lead to build → plan → devops approval → deploy.
- `vignemale secret set/list`, `vignemale logs`, `vignemale status`, `vignemale
  destroy`: API calls.
- `--local` kept: same engine, without server or approval (self-hosted / dev).

## 5. Migrations = a job in the customer account
Instead of loading the app on the deploy machine, the worker launches a **one-shot
job** (Scaleway Serverless Job) with **the app's image**, which runs
`vignemale migrate` against the database (schema + `CREATE EXTENSION vector` + .sql).
Benefits: same deps as prod, no laptop coupling, idempotent, traced.

## 6. Technical choices (and why)
- **Engine + control plane language: Go** (decided) — type-safe for the
  reconciler's state machine / the API / the jobs; **`scaleway-sdk-go` is the
  reference SDK** (the `scw` CLI derives from it) and covers everything (`serverless_sqldb`,
  `container`, `rdb`, `secret`, `registry`, `iam`, `jobs`, `cockpit`, `billing`);
  a single static binary, goroutines for the worker model. The Python
  `vignemale-deploy` remains the **validated PoC** whose design transposes.
- **Build: server-side** (decided) — the client pushes the source, the platform
  builds (BuildKit, native amd64) and pushes the image to the customer's registry,
  Encore-style. Accepted cost: a **build farm** to operate (see §8). `collect` (Python)
  lives in that build and emits the `meta` → Go stays pure.
- **Queue: Postgres `SKIP LOCKED`** — no broker to operate, transactional with
  the state; it is also the product's `queue` primitive (reused).
- **Multi-region/provider**: the reconciler abstracts the provider (Go interface);
  region = environment config. OVH later via a 2nd provider.
- **CLI**: remains the Python tool for **local dev** (`run`/`check`/`gen` —
  coupled to the Python runtime); for the **cloud**, a thin client (Go or Python)
  that pushes the source and streams. To be decided separately.

## 7. PoC → prod path (proposed order)
1. **Rewrite the engine in Go** (`vignemale-engine` in Go): a clean reconciler
   (`State` interface, desired/actual diff, lock, rollback, persisted IDs) on
   `scaleway-sdk-go`. The Python PoC serves as the spec. Testable via the CLI's
   `--local` (the Go binary invoked with a `meta` + an image).
2. **Migrations as in-account Serverless Jobs** (kills the local-loading hack).
3. **Build service**: collect(Python)+BuildKit worker → amd64 image + meta.
4. **Go control plane skeleton**: API + Postgres (model in 3.4) + `vignemale
   login` + a `deploy` endpoint that *enqueues* the build.
5. **Async workers** (build + deploy) + SSE stream + encrypted secrets/creds.
6. **Dashboard, observability (Cockpit), billing, multi-region.**

(1) and (2) de-risk the Go foundation without yet operating the server or the build
farm. (3) introduces the build farm. (4-5) assemble the control plane around it.

## 7bis. Repository split (open-core)
DECIDED: **open-core** model, two repositories.
- **`vignemale` (this repo, open-source, MPL planned)**: the runtime (Rust core +
  Python SDK), the dev CLI (`run`/`check`/`gen`/`build`), `vignemale-deploy` (the
  Python PoC that serves as the spec), the docs. What the user installs.
- **`vignemale-cloud` (NEW, private)**: the **Go engine** (reconciler on
  `scaleway-sdk-go`), the **control plane** (API, jobs, git-receive, build
  service), the **web panel**. The commercial product — never published.
The contract between the two = the **`meta` proto** (produced on the open-source
side by `collect`, consumed on the private side by the Go engine).

## 8. Open questions
- **Bootstrap**: the Go control plane is deployed **by hand on Scaleway**
  (decided — no circular dependency on Vignemale). A simple instance/container
  + managed Postgres for its own state.
- **Build farm**: where does BuildKit run? (dedicated Scaleway instance, or k8s
  Kapsule, or a Serverless Job with rootless BuildKit). DinD/cache/security to
  be scoped. This is the heaviest component to operate.
- **Cloud CLI: Go or Python?** (local dev stays Python). To be decided.
- **Receiving the `git push vignemale`**: DECIDED — **the control plane hosts the
  git remote** (smart-HTTP `git-receive-pack`, Heroku-style); the push triggers
  the build. `vignemale login` sets this remote.
- **Notifications**: email (SMTP/Scaleway TEM) to the dev; panel alert to the devops
  (in-app + optional email/Slack).
- **Creds/secrets rotation**: policy and UX.
- **GDPR compliance**: cross-reference with the `vignemale gdpr` tooling already in place.
