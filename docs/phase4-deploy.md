# Phase 4 — Provisioning & deploy Scaleway (note de conception)

> But : `vignemale deploy` → l'app tourne en prod sur le cloud EU, sans ops.
> C'est la partie 100 % différenciante (zéro code Encore à copier — leur
> control-plane est propriétaire).

## 1. L'atout : presque tout est déjà prêt

L'architecture a été pensée pour ce moment. La Phase 4 ne réinvente rien, elle
**branche** ce qui existe :

- **Le meta** (`collect`) est déjà l'inventaire des ressources : services +
  endpoints, `SQLDatabase`, `Bucket`, `Secret`, `auth_handler`. Le deploy lit
  ce graphe et sait quoi créer.
- **Le provider switch** : tout le runtime se configure par env vars
  (`VIGNEMALE_SQLDB_*`, `VIGNEMALE_S3_*`, `VIGNEMALE_SECRET_*`,
  `VIGNEMALE_SERVICE_*`, `VIGNEMALE_SERVICE_SECRET`). **Le deploy n'a qu'à poser
  ces variables** — aucune logique cloud dans le runtime.
- **Prod-ready** : healthz (`/__vignemale/healthz`), drain + `keep_accepting`
  (la fenêtre LB existe déjà), multi-process (`VIGNEMALE_WORKERS`), logs JSON.
  Les Serverless Containers consomment tout ça tel quel.
- **Migrations** : `db.migrate()` existe, appliquées au démarrage.

## 2. Les briques Scaleway (vérifiées)

| Ressource Vignemale | Service Scaleway | Notes |
|---|---|---|
| `SQLDatabase` | Managed Database PostgreSQL | pgvector dispo ✓ ; 1 instance, N bases logiques (API ou SQL) |
| `Bucket` | Object Storage | S3-compatible → notre `Bucket` marche déjà via `VIGNEMALE_S3_*` |
| `Secret` | Secret Manager **ou** env secrètes du container | le plus simple : env secrètes du Serverless Container |
| chaque `Service` | Serverless Container | image depuis le Container Registry Scaleway |
| accès | IAM | clé API (application) pour le control-plane + creds runtime |

Accès programmatique : **SDK Python officiel `scaleway`** (cohérent avec notre
outillage Python), ou API HTTP, ou Terraform. Cf. §6.

## 3. Les trois commandes (dans `vignemale-cli`, pas le runtime)

```
vignemale build    → Dockerfile généré, image construite, poussée au registry
vignemale provision→ crée les ressources Scaleway depuis le meta (DB, buckets, secrets, IAM)
vignemale deploy   → push image + Serverless Container par service + env vars + migrations
```

`deploy` orchestre `build` + `provision` + la mise à jour des containers.

### Le Dockerfile (généré)

Multi-stage : (1) builder Rust + maturin → le wheel `vignemale` ; (2) `python-slim`
+ wheel + `vignemale-cli` + le code de l'app. Entrypoint :
`vignemale run /app` (ou `gateway` en multi-container). Healthz déjà exposé →
Scaleway sonde `/__vignemale/healthz`.

## 4. La décision structurante : mono-container vs multi-container

| | Mono-container (défaut proposé) | Multi-container |
|---|---|---|
| Forme | tous les services dans 1 container (`vignemale run`) | 1 container/service + 1 gateway |
| Appels inter-services | fonction directe (in-process) | HTTP signé svcauth (déjà construit) |
| Coût | 1 container facturé | N+1 containers |
| Scale | horizontal (instances) | par service, indépendant |
| Pour qui | la cible : déployer **un agent** simplement | grosses apps multi-équipes |

**Reco : mono-container par défaut, multi-container en opt-in.** La cible
(déployer un agent IA en 1 commande) veut la simplicité et le coût mini. On a
déjà la gateway + svcauth pour le jour où le multi-container est demandé — mais
ce n'est pas le défaut. Le provider switch fait que c'est le **même artefact**,
juste un découpage de déploiement différent.

## 5. État & idempotence

`deploy` doit être rejouable sans tout recréer. Deux options :
- **Tags + lookup** (robuste) : tagger chaque ressource Scaleway avec
  `vignemale-app=<nom>` + `vignemale-resource=<id>`, et la retrouver avant de
  créer. Pas de fichier d'état à dériver.
- **Fichier d'état** (`.vignemale/deploy.json`) : plus simple, mais peut
  diverger du réel.

Reco : tags + lookup (l'état vit dans Scaleway, source de vérité unique).

## 6. Décisions à trancher

1. **Compte Scaleway** : le provisioning réel exige une clé API IAM. Sans
   compte, on développe le driver + un **`--dry-run`** (affiche le plan, façon
   `terraform plan`, testable sans cloud) et on valide ensuite sur un vrai
   compte. Le dry-run a aussi une valeur produit.
2. **SDK vs Terraform** : SDK Python `scaleway` (tout en Python, cohérent) vs
   Terraform (déclaratif, état géré, mais dépendance + langage HCL).
   Reco : SDK Python, avec une **interface driver** (`provision_db`,
   `provision_bucket`, `build_push`, `deploy_service`, `set_secrets`) pour
   préparer le multi-cloud (OVH) plus tard.
3. **Mono vs multi-container par défaut** : cf. §4 (reco mono).
4. **Migrations au deploy** : un step `deploy` qui applique `migrate()` une
   fois (la CLI se connecte à la DB managée) avant de router le trafic — évite
   la course entre N instances.

## 7. Découpage en tranches livrables

1. **`vignemale build`** — Dockerfile + build local + (push registry). La
   partie build est testable sans compte (vérifier que l'image démarre et
   répond au healthz).
2. **Driver Scaleway + `provision --dry-run`** — planifie les ressources depuis
   le meta, affiche le plan. Testable sans compte.
3. **`provision` réel** — crée DB/buckets/secrets/IAM via le SDK. Compte requis.
4. **`deploy`** — push image + Serverless Container(s) + env vars + migrations.
   Compte requis.
5. **Idempotence (tags+lookup) + rollback basique + `vignemale logs/status`.**

Ordre : 1 et 2 d'abord (sans compte, dérisquent l'archi), puis 3-4 dès qu'un
compte Scaleway est dispo.

## 8. Outillage Scaleway — ne PAS réinventer la roue (analysé le 15 juin 2026)

Analyse du GitHub Scaleway (github.com/scaleway). Décision : l'`apply` est de la
**colle fine au-dessus du SDK Python officiel `scaleway`** (PyPI `scaleway`,
v2.11, Apache-2.0, ~beta-stable). Il couvre TOUS nos produits, et notre moteur
est déjà en Python (réutilisable tel quel par un control plane Python).

| Ressource Vignemale | Module SDK | Méthodes clés (apply + idempotence + progress) |
|---|---|---|
| instance Managed DB | `rdb.v1` `RdbV1API` | `create_instance` / `wait_for_instance` / `list_instances` (lookup tags) / `get_instance_certificate` / `get_instance_metrics` (observ.) |
| base logique | `rdb.v1` | `create_database` / `list_databases` / `create_user` / `list_privileges` |
| bucket | **aucun module** → S3 | Object Storage = S3 pur : on **réutilise notre code Rust `aws-sdk-s3`** (`bucket_op`), pas de SDK Scaleway |
| secret | `secret.v1beta1` `SecretV1Beta1API` | `create_secret` / `create_secret_version` / `access_secret_version` / `list_secrets` |
| Serverless Container | `container.v1beta1` `ContainerV1Beta1API` | `create_namespace` / `create_container` / `update_container` / `deploy_container` / `wait_for_container` / `list_containers` (lookup) / `create_domain` |
| Container Registry | `registry.v1` | namespace pour pousser l'image d'app |
| IAM / creds | `iam.v1alpha1` | clés d'accès déléguées du client |
| observabilité | `cockpit` | logs/métriques agrégés (argument de vente) |

**SDK vs Terraform** : on garde le SDK (pas Terraform/Crossplane). Le SDK mappe
1-pour-1 sur nos `Action`s, donne `wait_for_*` (→ stream de progression pour le
log de deploy) et `list_*` (→ idempotence par tags, notre design). Terraform
rajouterait génération HCL + binaire + backend d'état à gérer = réinventer le
travail du control plane dans un autre outil.

**Pattern d'orchestration** (repris de leur `serverless-api-framework-python`,
qui vise les Functions mais montre la bonne séquence) : *get-or-create namespace
→ create/update idempotent (lookup) → deploy → wait → nettoyage du périmé*.

**Conséquence** : `ScalewayProvider.existing()` = `list_*` filtré par tags ;
`apply()` = les `create_*`/`deploy_*` ci-dessus ; le control plane peut être en
**Python** pour réutiliser `vignemale-deploy` + `scaleway` directement.

### Repos Scaleway utiles
- SDK Python `scaleway` (notre dépendance d'apply) : github.com/scaleway/scaleway-sdk-python
- `scw` CLI (Go, fallback shell pour `--local`) : github.com/scaleway/scaleway-cli
- `serverless-api-framework-python` (référence de flux deploy) : github.com/scaleway/serverless-api-framework-python
- provider Terraform (écarté, mais réf. de mapping) : github.com/scaleway/terraform-provider-scaleway

## Sources
- Serverless Containers : https://www.scaleway.com/en/developers/api/serverless-containers
- Deploy container (API) : https://www.scaleway.com/en/docs/serverless-containers/api-cli/deploy-container-api/
- Managed Database PostgreSQL : https://www.scaleway.com/en/developers/api/managed-database-postgre-mysql
- SDK Python : https://github.com/scaleway/scaleway-sdk-python
