# Vignemale Cloud — architecture du control plane

> État au 15 juin 2026 : le chemin de déploiement est **prouvé en prod**
> (`vignemale deploy` → app + base serverless live sur Scaleway), mais piloté
> depuis le laptop = **PoC**. Ce document décrit le **control plane** (le serveur
> qui fait de Vignemale une plateforme) et le chemin PoC → prod.

## 1. Du PoC au produit — ce qui doit changer

| Aspect | PoC actuel | Cible |
|---|---|---|
| Orchestration | sur le laptop (CLI) | **serveur** (control plane), CLI = client mince |
| État | fichier JSON local | **Postgres** = source de vérité (apps, envs, ressources, deploys) |
| Creds cloud / secrets | variables d'env du shell | **chiffrés** dans le control plane, injectés au deploy |
| Migrations | chargement de l'app en local | **job dans le compte client** avec l'image de l'app |
| Reconcile | `ensure_*` best-effort | **diff désiré/réel, lock, rollback, retries** |
| Identité / équipe / billing | inexistants | **login, orgs/RBAC, audit, metering** |

Le moteur `vignemale-deploy` (engine + ScalewayProvider) **reste** : il devient le
cœur exécuté par les workers du control plane (et toujours appelable en `--local`).

## 2. Vue d'ensemble

```
  Dev / CI                Control plane (Vignemale Cloud)          Compte Scaleway DU CLIENT
 ┌─────────┐   HTTPS    ┌───────────────────────────────┐        ┌────────────────────────┐
 │ vignemale│  +token   │  API  ──►  Postgres (état)     │  SDK   │  Serverless Container  │
 │   CLI    │──────────►│   │         + file de jobs     │ Scaleway│  Serverless SQL DB     │
 │ (login,  │   SSE     │   ▼        (SKIP LOCKED)       │───────►│  Object Storage        │
 │  deploy) │◄──────────│ Workers ──► moteur réconcili.  │  creds  │  Container Registry    │
 └─────────┘  (logs)    │            (vignemale-deploy)  │ délégués└────────────────────────┘
                        │  Secrets/creds chiffrés · RBAC │
                        └───────────────────────────────┘
                          Dashboard équipe (web) ───────┘
```

**Modèle (rappel, décidé)** : BYOC + control plane managé qui facture. On déploie
**toujours dans le compte Scaleway du client** (clé IAM déléguée qu'il connecte) ;
le control plane garde le contrôle (état, RBAC, secrets, audit) et facture le
**service de plateforme**, pas la compute.

## 3. Le control plane en détail

### 3.1 Responsabilités
- Authentifier (users, orgs, tokens) et autoriser (RBAC).
- Détenir, **chiffrées**, les credentials cloud et les secrets applicatifs.
- Recevoir une requête de deploy, l'exécuter de façon **asynchrone, idempotente,
  reprenable**, et **streamer** la progression.
- Être la **source de vérité** : ce qui est déployé, où, dans quel état, avec
  quel historique (rollback).
- Agréger l'observabilité ; mesurer l'usage (billing).

### 3.2 Composants
1. **API** (Python — réutilise `vignemale-deploy` + SDK Scaleway ; dogfood-able
   plus tard en app Vignemale). REST + SSE pour les logs.
2. **Postgres** : l'état (cf. 3.4).
3. **File de jobs** : `SELECT … FOR UPDATE SKIP LOCKED` sur une table `jobs`
   (même primitive que la future `queue`). Un deploy = un job.
4. **Workers** : dépilent les jobs, exécutent le **moteur de réconciliation**,
   écrivent la progression (steps) lue par l'API en SSE.
5. **Secrets/creds** : chiffrement enveloppe (clé maître → clés data), déchiffrés
   *juste-à-temps* par le worker pour l'injection container. Jamais exposés au CLI.
6. **Dashboard** (web) : apps, envs, deploys, secrets, logs, membres.

### 3.3 Cycle de vie d'un deploy

```
CLI: vignemale deploy --env prod              (token Bearer)
  └─ build+push image → digest                (v1 : CLI build ; v2 : build serveur)
  └─ POST /apps/{app}/envs/{env}/deploys { meta, image_digest, config }
        └─ control plane : crée deployments(row, status=queued) + enqueue job → renvoie {deploy_id}
  └─ GET /deploys/{id}/events  (SSE: stream des steps)

Worker (dépile le job) :
  1. lock advisory sur (env_id)        ← un seul deploy concurrent par env
  2. charge creds client (déchiffre) + état (table resources)
  3. PLAN = reconcile(meta désiré vs état+Scaleway)        [diff: create/update/noop/delete]
  4. APPLY ressources (DB serverless, buckets, secrets)    [idempotent, IDs → table resources]
  5. MIGRATE : job one-shot dans le compte client, image de l'app, `vignemale migrate`
  6. ROLLOUT container (nouvelle révision) → health check
  7. bascule trafic → status=succeeded   (sinon ROLLBACK révision précédente, status=failed)
  8. release lock ; chaque étape écrite dans deployment_steps (→ SSE)
```

### 3.4 Modèle de données (esquisse Postgres)
```
orgs(id, name, plan)                       users(id, email)
memberships(user_id, org_id, role)         api_tokens(id, org_id, hash, scopes)
cloud_credentials(id, org_id, provider, enc_blob, scopes)   -- clé IAM client chiffrée
apps(id, org_id, name)
environments(id, app_id, name, region, db_backend)
secrets(id, env_id, name, enc_value, version)               -- chiffrés
resources(id, env_id, kind, logical_name, provider_id, meta)-- registre des ressources Scaleway
deployments(id, env_id, image_digest, meta_json, status, created_by, created_at, finished_at, error)
deployment_steps(id, deployment_id, seq, name, status, message, ts)   -- progression (SSE)
jobs(id, type, payload, status, attempts, run_after, locked_by, locked_at)  -- file SKIP LOCKED
audit_log(id, org_id, actor, action, target, ts)
```
La table `resources` remplace le fichier JSON local : elle porte les IDs Scaleway
et le peu de secret non-récupérable (mot de passe RDB en mode managed ; le mode
serverless par défaut n'en a pas, auth IAM).

### 3.5 Sécurité (le point dur)
- **Creds cloud du client** : une clé IAM **scopée au minimum** (RDB, Object
  Storage, Containers, Registry, Secret, IAM-read), chiffrée au repos.
- **Chiffrement enveloppe** : clé maître (Scaleway Key Manager / KMS) → clés data
  par org ; le worker déchiffre en mémoire, jamais en log.
- **Isolation tenant** : toute requête est scopée à l'org du token ; pas de compute
  multi-tenant partagé à isoler (BYOC) — l'isolation porte sur l'état + les secrets.
- **Audit** : toute action sensible (deploy, lecture secret, rotation creds) loggée.
- **CLI** : ne détient qu'un token court ; ni creds cloud ni secrets en local.

### 3.6 Concurrence & idempotence
- **Un deploy à la fois par (app, env)** : advisory lock Postgres ; les autres
  attendent ou sont rejetés.
- **Idempotent** : chaque étape vérifie l'état (table `resources` + lookup
  Scaleway par tags) avant d'agir → un job repris ne double rien.
- **Rollback** : sur échec après le rollout, repasser le container à la révision
  précédente (Scaleway garde les révisions) ; les ressources créées restent (sûr).

## 4. Le CLI devient un client mince
- `vignemale login` : device-flow OAuth → token dans `~/.vignemale/auth.json`.
- `vignemale deploy` : build+push (v1) → POST au control plane → stream SSE.
- `vignemale secret set/list`, `vignemale logs`, `vignemale status`, `vignemale
  destroy` : appels API.
- `--local` conservé : même moteur, sans serveur (self-hosted / open-source).

## 5. Migrations = job dans le compte client
Au lieu de charger l'app sur la machine de deploy, le worker lance un **job
one-shot** (Serverless Job Scaleway) avec **l'image de l'app**, qui exécute
`vignemale migrate` contre la base (schéma + `CREATE EXTENSION vector` + .sql).
Avantages : mêmes deps que la prod, pas de couplage laptop, idempotent, tracé.

## 6. Choix techniques (et pourquoi)
- **Langage control plane : Python** — réutilise `vignemale-deploy` + SDK Scaleway
  tels quels ; un seul moteur ; dogfood-able. (Go envisageable pour la perf, mais
  réécrirait l'engine.)
- **File : Postgres `SKIP LOCKED`** — pas de broker à opérer, transactionnel avec
  l'état ; c'est aussi la primitive `queue` du produit (réutilisée).
- **Build : CLI (v1) → serveur depuis git (v2)** — commencer simple (digest
  envoyé), évoluer vers « git push = deploy ».
- **Multi-région/provider** : l'engine abstrait déjà le provider ; région = config
  d'environnement. OVH plus tard via un 2ᵉ provider.

## 7. Chemin PoC → prod (ordre proposé)
1. **Durcir le moteur en reconciler** : interface `State` (au lieu du fichier
   JSON), diff désiré/réel, lock, rollback, IDs persistés. Testable en `--local`.
2. **Migrations en job in-account** (tue le hack de chargement local).
3. **Squelette control plane** : API + Postgres (modèle 3.4) + `vignemale login` +
   endpoint `deploy` qui *enqueue* (pas encore le worker complet).
4. **Workers + jobs async** + stream SSE + secrets/creds chiffrés server-side.
5. **Dashboard, observabilité (Cockpit), billing, multi-région.**

Chaque étape convertit une partie du PoC. (1) et (2) sont sans serveur — on
dérisque la fondation avant de bâtir le control plane autour.

## 8. Questions ouvertes
- **Bootstrap / dogfooding** : le control plane est une app Vignemale → le 1er
  deploy se fait à la main (poule/œuf). À assumer et documenter.
- **Build serveur-side** (v2) : build farm BuildKit à opérer — coût.
- **Rotation des creds/secrets** : politique et UX.
- **Conformité RGPD** : croiser avec l'outillage `vignemale rgpd` déjà en place.
