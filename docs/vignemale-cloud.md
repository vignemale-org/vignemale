# Vignemale Cloud — plateforme de déploiement (note de conception)

> Vision : `vignemale login` + `vignemale deploy` → une équipe gère ses
> déploiements depuis une plateforme. Le CLI parle à un **control plane**
> (serveur Vignemale) qui orchestre le provisioning + le deploy sur le cloud EU.

## 1. La bascule d'architecture (≠ Phase 4 "BYOC-laptop")

La Phase 4 esquissée mettait l'orchestration Scaleway **dans le CLI**, exécutée
depuis le laptop du dev. Le modèle "Cloud / équipe" **centralise** cette
orchestration dans le control plane. Conséquences :

- **Le moteur d'orchestration (driver Scaleway) vit côté serveur**, pas dans le
  CLI. Le CLI devient un **client mince** : authentifie, envoie une requête de
  deploy, streame les logs en retour.
- Bénéfices : audit centralisé, RBAC, cohérence (un seul moteur), **pas de
  credentials cloud sur les laptops**, secrets centralisés et chiffrés.
- On garde un mode **self-hosted / open-source** : le même moteur tourne en
  local via `vignemale deploy --local` (BYOC depuis le laptop), pour ceux qui ne
  veulent pas de la plateforme.

→ Décision dérivée : le **driver Scaleway** (tranches 2-4 de Phase 4) doit être
une **brique réutilisable** appelée soit par le control plane, soit par le CLI
en `--local`.

## 2. Anatomie du control plane

| Brique | Rôle |
|---|---|
| **API** (REST/gRPC) | sert le CLI + le dashboard web équipe |
| **Identité** | users, teams/orgs, **RBAC**, tokens (login par device-flow) |
| **Modèle** | apps, **environnements** (staging/prod), config + secrets par env |
| **Orchestrateur** | **file de jobs** (deploy = async, multi-étapes, idempotent, retries) + workers qui appellent Scaleway |
| **État** | ressources provisionnées (IDs Scaleway), historique de rollout, rollback |
| **Store** | Postgres (état) + **chiffrement des secrets au repos** |
| **Observabilité** | agrégation logs/métriques (l'argument de vente différenciant) |

## 3. Décisions structurantes

1. **Que envoie `vignemale deploy` ?**
   - (a) *CLI build+push l'image* (on a déjà le build rapide + image de base),
     puis envoie `meta + digest d'image + config` au serveur, qui
     provisionne+déploie. **Serveur léger. Reco MVP.**
   - (b) *Serveur build depuis git* (façon Encore : push = build serveur-side,
     reproductible). Plus "vraie plateforme" mais nécessite une **build farm**.
   → Démarrer en (a), garder (b) comme évolution.

2. **Où vont les déploiements ? — DÉCIDÉ : BYOC + control plane managé qui facture.**
   On déploie **toujours dans le compte Scaleway du CLIENT** (il connecte une clé
   IAM restreinte), mais l'orchestration passe par le **serveur distant** qui
   garde le contrôle (RBAC, état, secrets, audit, historique). Vignemale **facture
   le service de plateforme** (orchestration + management + observabilité), pas la
   compute (que le client paie directement à Scaleway).
   - Avantage : pas de statut de revendeur cloud, pas d'infra compute partagée à
     isoler, mais on garde un produit facturable et le contrôle.
   - Sécurité : le control plane détient les **creds Scaleway de N clients** → le
     chiffrement au repos + l'isolation des secrets par tenant restent critiques
     (mais pas d'isolation de compute multi-tenant à gérer).
   - `--local` : le même moteur tourne sur le laptop sans serveur (open-source /
     self-hosted), déployant dans le même compte client.

3. **Login** : OAuth **device-flow** (`vignemale login` ouvre le navigateur,
   confirme un code) → token dans `~/.vignemale/auth.json`. Le CLI joint le token
   à chaque requête.

4. **Sécurité — LE point dur** : le control plane détient les creds cloud et les
   secrets de N équipes. Chiffrement au repos (KMS/enveloppe), IAM
   least-privilege par app/env, **isolation tenant**, audit log, rotation des
   secrets. C'est le chantier critique dès qu'on passe en managé multi-tenant.

## 4. Le protocole de deploy (esquisse)

```
vignemale login                         # device-flow → token local
vignemale deploy --env prod             # build+push image, POST au control plane

POST /apps/{app}/envs/{env}/deploys
  { meta, image_digest, config }  ->  { deploy_id }
GET  /deploys/{deploy_id}/logs (stream SSE)

Étapes serveur (job idempotent, repris en cas d'échec) :
  valider meta → diff vs état → provisionner (DB/bucket/secrets manquants) →
  migrations → rollout container (Serverless Container) → health →
  bascule trafic → done   (sinon rollback automatique)
```

Le `meta` (déjà produit par `collect`) est l'entrée : il dit au serveur quelles
ressources existent à réconcilier. Le **provider switch** fait que déployer =
créer les ressources puis **poser les `VIGNEMALE_*`** sur le container.

## 5. Stack du control plane

API + Postgres (état) + file de jobs + workers Scaleway + dashboard web.
Bootstrap avec une stack stable ; **dogfood en Vignemale** une fois mûr (le
control plane est lui-même une app Vignemale déployable sur Scaleway). Attention
au bootstrap (poule/œuf : le premier deploy se fait à la main).

## 6. Chemin par phases

1. **`vignemale login`** (device-flow) + control plane minimal (auth, modèle
   apps/envs) — sans deploy réel. Valide l'identité et le lien CLI↔serveur.
2. **Moteur d'orchestration Scaleway** (le driver de Phase 4), réutilisable :
   `provision --dry-run` → réel. Appelable en `--local` ou par le serveur.
3. **`vignemale deploy`** : CLI build+push image → POST au serveur → orchestration
   → stream des logs. Bout-en-bout sur une vraie app.
4. **Dashboard équipe** : RBAC, envs, secrets, historique, rollback.
5. **Observabilité agrégée** (logs/métriques). Puis évolution **managed
   multi-tenant + billing**.

## 7. Risques / questions ouvertes

- **Managed multi-tenant** = sécurité + billing + conformité : très lourd, à ne
  pas sous-estimer (≠ BYOC).
- **Build serveur-side** (option 1b) = build farm à opérer.
- **Bootstrap/dogfood** du control plane.
- Réutilisabilité du driver Scaleway entre CLI `--local` et serveur : à concevoir
  comme une lib dès la tranche 2 de Phase 4.

## Lien avec l'existant
- `docs/phase4-deploy.md` reste valable pour le **moteur d'orchestration** ; ce
  document décide seulement **où il tourne** (serveur centralisé vs laptop) et
  **comment l'équipe pilote** (login + plateforme).
- L'image de base CI (`docker/runtime.Dockerfile` + workflow) sert l'option 1a :
  le CLI produit vite une image multi-arch poussable vers le registry cible.
