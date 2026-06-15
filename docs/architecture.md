# Vignemale Cloud — architecture du control plane

> État au 15 juin 2026 : le chemin de déploiement est **prouvé en prod**
> (`vignemale deploy` → app + base serverless live sur Scaleway), mais piloté
> depuis le laptop = **PoC**. Ce document décrit le **control plane** (le serveur
> qui fait de Vignemale une plateforme) et le chemin PoC → prod.

## 1. Du PoC au produit — ce qui doit changer

| Aspect | PoC actuel | Cible |
|---|---|---|
| Langage moteur/serveur | Python (`vignemale-deploy`) | **Go** (type-safe, `scaleway-sdk-go` de référence) |
| Orchestration | sur le laptop (CLI) | **serveur** (control plane), CLI = client mince |
| Build de l'image | manuel sur le laptop (`--from-source` émulé) | **server-side** (push le code → la plateforme build + déploie) |
| État | fichier JSON local | **Postgres** = source de vérité (apps, envs, ressources, deploys) |
| Creds cloud / secrets | variables d'env du shell | **chiffrés** dans le control plane, injectés au deploy |
| Migrations | chargement de l'app en local | **job dans le compte client** (Serverless Job Go SDK) avec l'image de l'app |
| Reconcile | `ensure_*` best-effort | **diff désiré/réel, lock, rollback, retries** |
| Identité / équipe / billing | inexistants | **login, orgs/RBAC, audit, metering** |
| Gouvernance | deploy = immédiat | **approbation devops SYSTÉMATIQUE** (panel web Vignemale) : plan revu + validé avant tout apply ; mail au dev |
| Déclencheur deploy | CLI manuel | `vignemale deploy` **ou** `git push vignemale` |

**Décision (15 juin 2026) : le moteur de déploiement et le control plane sont en
Go.** Le `vignemale-deploy` Python qu'on a écrit était le **PoC qui a dérisqué le
flux en prod réelle** (appels SDK, format DSN serverless, mapping ressources,
migrations) — ce savoir se transpose tel quel en Go. Le SDK Go couvre tous nos
produits (`serverless_sqldb`, `container`, `rdb`, `secret`, `registry`, `iam`,
`jobs`, `cockpit`, `billing`).

**La frontière Python/Go** : `collect` (extraction du `meta`) parse du Python
(griffe) → **reste en Python**, mais s'exécute **dans l'étape de build** (qui a de
toute façon le source + Python) et **émet le `meta` comme artefact**. Le control
plane Go ne consomme que le `meta` (proto, déjà language-agnostique) + l'image →
**Go ne touche jamais à Python.**

## 2. Vue d'ensemble

```
  Dev / CI                 Control plane Vignemale Cloud (Go)        Compte Scaleway DU CLIENT
 ┌─────────┐  push code  ┌────────────────────────────────────┐    ┌────────────────────────┐
 │ vignemale│  + token   │  API (Go) ──► Postgres (état)        │SDK │  Serverless Container  │
 │   CLI    │───────────►│   │           + file jobs SKIP LOCKED │ Go │  Serverless SQL DB     │
 │ (login,  │   SSE      │   ├─► BUILD worker (Python collect    │───►│  Object Storage        │
 │  deploy) │◄───────────│   │     + BuildKit) → image + meta    │creds│  Container Registry    │
 └─────────┘  (logs)     │   └─► DEPLOY worker (Go) ─► reconcile │délég.  Serverless Job (migrations)
                         │  Secrets/creds chiffrés · RBAC        │    └────────────────────────┘
                         └────────────────────────────────────┘
         Panel admin web (NOTRE produit SaaS : approbation + gestion) ─────┘
```
Deux types de worker : **build** (a besoin de Python+griffe pour `collect` et de
BuildKit pour l'image ; produit l'image + le `meta` + le plan) et **deploy** (Go
pur : réconciliation Scaleway). Le `meta` (proto) est le contrat entre les deux.

### 2.1 Le parcours (la vraie UX)

**Développeur :**
1. `uv add vignemale` — installe la lib dans son app.
2. `vignemale login` — s'authentifie et **rattache l'app à son projet entreprise**
   (org/env). Au passage, **initialise le remote git `vignemale`**.
3. `vignemale run` — développe en local (l'agent tourne, infra locale auto).
4. Déploie : `vignemale deploy` **ou** `git push vignemale`.

**DevOps (panel admin) :**
5. Reçoit une **notification** : « un dev a poussé sur le projet X ».
6. Voit **exactement ce qui va être appliqué** : le diff des ressources + les
   **paramètres configurés au niveau entreprise** (région, scaling, budget,
   secrets, quotas).
7. **Accepte ou refuse.**
8. À l'acceptation : déploiement → **mail au dev** (succès + URL, ou échec).

C'est une **gate de gouvernance** : rien n'atterrit sur le cloud sans qu'un
responsable ait vu et validé le plan. Différenciateur entreprise fort (contrôle
+ visibilité + RGPD), et ça réutilise directement le **plan** du reconciler.

**Modèle (rappel, décidé)** : BYOC + control plane managé qui facture. On déploie
**toujours dans le compte Scaleway du client** (clé IAM déléguée qu'il connecte) ;
le control plane garde le contrôle (état, RBAC, secrets, audit) et facture le
**service de plateforme**, pas la compute.

## 3. Le control plane en détail

### 3.1 Responsabilités
- Authentifier (users, orgs, tokens) et autoriser (**RBAC** : dev / devops / admin).
- Détenir, **chiffrées**, les credentials cloud et les secrets applicatifs.
- Recevoir un déclencheur (`vignemale deploy` ou `git push`), **builder**,
  **calculer le plan**, puis le soumettre à la **gate d'approbation devops**.
- Après approbation, exécuter le deploy de façon **asynchrone, idempotente,
  reprenable**, **streamer** la progression, et **notifier** (mail dev).
- Être la **source de vérité** : ce qui est déployé, où, dans quel état, avec
  quel historique (rollback) et **quelle décision d'approbation** (audit).
- Agréger l'observabilité ; mesurer l'usage (billing).

### 3.2 Composants
1. **API** (Go) — REST + SSE pour les logs ; scope par org/token.
2. **Postgres** : l'état (cf. 3.4).
3. **File de jobs** : `SELECT … FOR UPDATE SKIP LOCKED` sur une table `jobs`
   (même primitive que la future `queue`). Un deploy = un build job puis un deploy job.
4. **Build worker** : Python (collect/griffe) + BuildKit → image amd64 + `meta`.
5. **Deploy worker** (Go) : dépile, exécute le **reconciler** (`scaleway-sdk-go`),
   écrit la progression (steps) lue par l'API en SSE.
6. **Secrets/creds** : chiffrement enveloppe (clé maître → clés data), déchiffrés
   *juste-à-temps* par le worker pour l'injection container. Jamais exposés au CLI.
7. **Panel admin** = **NOTRE produit web hébergé** (l'UI SaaS de Vignemale Cloud) :
   le devops y reçoit les notifications, **review le plan + les params entreprise,
   approuve/refuse**, et gère apps/envs/secrets/logs/membres. C'est la face visible
   du control plane (le CLI ne fait que dev + déclencher).

### 3.3 Cycle de vie d'un deploy

```
DÉCLENCHEUR (au choix) :
  vignemale deploy             → pousse le SOURCE au control plane
  git push vignemale           → le remote « vignemale » (posé au login) reçoit le push
        └─ control plane : deployments(status=queued) + enqueue BUILD job → {deploy_id}

BUILD worker (Python collect + BuildKit) :
  b1. collect (griffe) → meta (proto)                      [extraction statique]
  b2. docker build (amd64 natif) → push registre client → image_digest
  b3. PLAN = reconcile(meta vs état+Scaleway, fusionné avec la CONFIG ENTREPRISE)
            [diff create/update/delete + params org : région, scaling, budget, secrets]
  b4. status = pending_approval → NOTIFIE le panel admin (le devops)

GATE D'APPROBATION (humaine) :
  le devops voit dans le panel : le diff exact + les paramètres entreprise appliqués
  ├─ REJETÉ   → status=rejected, mail au dev (raison)
  └─ APPROUVÉ → enqueue DEPLOY job

DEPLOY worker (Go) :
  1. lock advisory sur (env_id)        ← un seul deploy concurrent par env
  2. charge creds client (déchiffre) + état (table resources)
  3. APPLY ressources (DB serverless, buckets, secrets)    [idempotent, IDs → table resources]
  4. MIGRATE : Serverless Job dans le compte client, image de l'app, `vignemale migrate`
  5. ROLLOUT container (nouvelle révision) → health check
  6. bascule trafic → status=succeeded   (sinon ROLLBACK révision précédente, status=failed)
  7. release lock ; steps écrits en continu (→ SSE panel) ; MAIL au dev (succès/échec + URL)
```
Le **plan est calculé AVANT l'approbation** (fin du build) : c'est lui que le
devops review. La **config entreprise** (région autorisée, scaling, budget,
secrets, quotas) est définie au niveau org/env et **fusionnée** avec ce que l'app
déclare — l'app exprime l'intention, l'org cadre. **L'approbation est SYSTÉMATIQUE**
(décidé) : tout deploy, quel que soit l'env, passe par `pending_approval` — pas de
bypass. C'est la garantie de gouvernance.

### 3.4 Modèle de données (esquisse Postgres)
```
orgs(id, name, plan)                       users(id, email)
memberships(user_id, org_id, role)         api_tokens(id, org_id, hash, scopes)  -- role: dev | devops | admin
cloud_credentials(id, org_id, provider, enc_blob, scopes)   -- clé IAM client chiffrée
apps(id, org_id, name, git_repo)                            -- git_repo : remote « vignemale »
environments(id, app_id, name, region, db_backend)          -- approbation systématique (pas de flag)
env_config(env_id, key, value)            -- params ENTREPRISE : région, scaling, budget, quotas…
secrets(id, env_id, name, enc_value, version)               -- chiffrés
resources(id, env_id, kind, logical_name, provider_id, meta)-- registre des ressources Scaleway
deployments(id, env_id, source_ref, image_digest, meta_json, plan_json, status, created_by, created_at, finished_at, error)
   -- status: queued→building→pending_approval→(approved|rejected)→deploying→(succeeded|failed)
approvals(id, deployment_id, decided_by, decision, reason, ts)         -- la gate devops
deployment_steps(id, deployment_id, seq, name, status, message, ts)    -- progression (SSE)
notifications(id, deployment_id, kind, to, status, ts)                 -- mail dev / alerte panel
jobs(id, type, payload, status, attempts, run_after, locked_by, locked_at)  -- file SKIP LOCKED
audit_log(id, org_id, actor, action, target, ts)
```
`plan_json` (le diff calculé au build) est ce que le devops review ; `approvals`
trace la décision ; `env_config` porte les paramètres entreprise fusionnés au plan.
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
- `vignemale login` : device-flow OAuth → token dans `~/.vignemale/auth.json`,
  rattache l'app au projet entreprise, et **pose le remote git `vignemale`**.
- Déclencher un deploy : `vignemale deploy` (pousse le source) **ou** `git push
  vignemale`. Les deux mènent à build → plan → approbation devops → deploy.
- `vignemale secret set/list`, `vignemale logs`, `vignemale status`, `vignemale
  destroy` : appels API.
- `--local` conservé : même moteur, sans serveur ni approbation (self-hosted / dev).

## 5. Migrations = job dans le compte client
Au lieu de charger l'app sur la machine de deploy, le worker lance un **job
one-shot** (Serverless Job Scaleway) avec **l'image de l'app**, qui exécute
`vignemale migrate` contre la base (schéma + `CREATE EXTENSION vector` + .sql).
Avantages : mêmes deps que la prod, pas de couplage laptop, idempotent, tracé.

## 6. Choix techniques (et pourquoi)
- **Langage moteur + control plane : Go** (décidé) — type-safe pour la machine à
  états du reconciler / l'API / les jobs ; **`scaleway-sdk-go` est le SDK de
  référence** (le `scw` CLI en dérive) et couvre tout (`serverless_sqldb`,
  `container`, `rdb`, `secret`, `registry`, `iam`, `jobs`, `cockpit`, `billing`) ;
  binaire statique unique, goroutines pour le modèle worker. Le `vignemale-deploy`
  Python reste le **PoC validé** dont le design se transpose.
- **Build : server-side** (décidé) — le client pousse le source, la plateforme
  build (BuildKit, amd64 natif) et pousse l'image au registre du client, façon
  Encore. Coût assumé : une **build farm** à opérer (cf. §8). `collect` (Python)
  vit dans ce build et émet le `meta` → Go reste pur.
- **File : Postgres `SKIP LOCKED`** — pas de broker à opérer, transactionnel avec
  l'état ; c'est aussi la primitive `queue` du produit (réutilisée).
- **Multi-région/provider** : le reconciler abstrait le provider (interface Go) ;
  région = config d'environnement. OVH plus tard via un 2ᵉ provider.
- **CLI** : reste l'outil Python pour le **dev local** (`run`/`check`/`gen` —
  couplés au runtime Python) ; pour le **cloud**, un client mince (Go ou Python)
  qui pousse le source et streame. À trancher séparément.

## 7. Chemin PoC → prod (ordre proposé)
1. **Réécrire le moteur en Go** (`vignemale-engine` Go) : reconciler propre
   (interface `State`, diff désiré/réel, lock, rollback, IDs persistés) sur
   `scaleway-sdk-go`. Le PoC Python sert de spec. Testable en CLI `--local` (le
   binaire Go invoqué avec un `meta` + une image).
2. **Migrations en Serverless Job in-account** (tue le hack de chargement local).
3. **Build service** : worker collect(Python)+BuildKit → image amd64 + meta.
4. **Squelette control plane Go** : API + Postgres (modèle 3.4) + `vignemale
   login` + endpoint `deploy` qui *enqueue* le build.
5. **Workers async** (build + deploy) + stream SSE + secrets/creds chiffrés.
6. **Dashboard, observabilité (Cockpit), billing, multi-région.**

(1) et (2) dérisquent la fondation Go sans encore opérer le serveur ni la build
farm. (3) introduit la build farm. (4-5) montent le control plane autour.

## 8. Questions ouvertes
- **Bootstrap** : le control plane Go se déploie **à la main sur Scaleway**
  (décidé — pas de dépendance circulaire à Vignemale). Simple instance/conteneur
  + Postgres managé pour son propre état.
- **Build farm** : où tourne BuildKit ? (instance Scaleway dédiée, ou k8s
  Kapsule, ou Serverless Job avec BuildKit rootless). DinD/cache/sécurité à
  cadrer. C'est le composant le plus lourd à opérer.
- **CLI cloud : Go ou Python ?** (le dev local reste Python). À trancher.
- **Réception du `git push vignemale`** : le control plane héberge un remote git
  (smart-HTTP `git-receive-pack` → déclenche le build), ou un dépôt Vignemale par
  app avec hook. Mécanisme à cadrer.
- **Notifications** : mail (SMTP/Scaleway TEM) au dev ; alerte panel au devops
  (in-app + mail/Slack optionnel).
- **Rotation des creds/secrets** : politique et UX.
- **Conformité RGPD** : croiser avec l'outillage `vignemale rgpd` déjà en place.
