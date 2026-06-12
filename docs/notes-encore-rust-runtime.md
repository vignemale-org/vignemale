# Notes — runtime Rust d'Encore (et implications Vignemale)

Sources : https://encore.dev/blog/rust-runtime · benchmarks https://encore.dev/blog/ai-benchmark
(lu le 12/06/2026)

## Pourquoi Rust (et pas un sidecar)

- Un prototype runtime Go en **sidecar** (process séparé, IPC) ajoutait **2-4 ms
  par requête** rien qu'en sérialisation + context switch, avant tout travail
  réel — abandonné. → le runtime doit être **in-process** avec le langage hôte.
  Nous : PyO3 in-process, même choix. ✅
- Rust = base réutilisable multi-langage (cf. prost, pydantic-core). Un futur
  SDK JS Vignemale réutiliserait le même core (notre pari : ORM piloté par
  descripteurs JSON, déjà language-agnostic).
- Node mono-thread → tout le non-métier (cycle HTTP, pool BD, pubsub, trace)
  tourne **multi-thread sur tokio**. Le GIL Python joue le rôle du mono-thread
  Node ; on relâche déjà le GIL (`allow_threads`) sur sqldb et les handlers
  tournent en `spawn_blocking`.

## Architecture (ce qu'on reproduit déjà)

- **2 protos** : *App Metadata* (compile-time, généré par le parser) +
  *Runtime Config* (deploy-time, choisit le provider). On a exactement ça :
  `collect` → meta.proto ; env vars `VIGNEMALE_*` posées au deploy. ✅
- Le runtime **ne parse jamais** le code — il reçoit une description structurée
  (collect vit dans la CLI, pas le runtime). ✅
- `CancellationGuard` : client déconnecté → le handler finit en tâche de fond
  pour émettre la fin de trace. Équivalent chez nous (timeout → handler continue
  en arrière-plan, logs conservés).
- 67 077 lignes de Rust côté Encore (core + bindings JS + tsparser + supervisor).

## Gateway Pingora — LA prochaine brique

- Encore **embarque Pingora dans le process runtime** (proxy HTTP de Cloudflare),
  pas un proxy séparé → zéro sérialisation à la frontière.
- Fournit : routage par path vers les services, **CORS**, **HTTP/2**, pooling de
  connexions, **graceful draining** — natif.
- **L'auth handler s'exécute DANS la gateway** : Pingora appelle le handler,
  récupère `{user_id, auth_data}`, puis forwarde au service backend avec
  l'identité propagée. C'est notre modèle exact (auth dans le core + svcauth
  signé) — il manque juste la *façade gateway* qui route vers N services.
- Encore a dû ajouter le support Windows à Pingora (contribué upstream) ; nous :
  Linux/macOS (containers) suffisent.

Implication : aujourd'hui notre multi-service tourne dans 1 process (appels =
fonctions) ou en HTTP signé service-à-service. La gateway est la pièce qui, en
prod, **reçoit le trafic public, authentifie à l'edge, route/forwarde vers le
bon service** — l'entrée unique d'une app déployée.

⚠️ Pingora pèse lourd (dépend de boringssl/pingora-core, gros arbre). Option de
départ pragmatique : gateway en **axum** (réutilise tout notre stack HTTP, CORS,
auth, svcauth, trace déjà écrits) ; passer à Pingora si un besoin réel le
justifie (HTTP/2 upstream, pooling fin). L'important est la *fonction* gateway,
pas la lib — l'interface reste la même.

## Benchmarks (méthodo à copier honnêtement)

- Outil : **oha**, 150 workers concurrents, 10 s, best-of-5.
- Deux scénarios : JSON simple **et** avec validation (l'écart explose là, car
  Encore valide au niveau Rust via les types du parser).
- Encore.ts : 121k req/s simple, 107k validation, P99 2.3/3.6 ms.
  Express+Zod : 15.7k / 11.9k, P99 11.9/18.2 ms → « 9× Express, -80% latence ».
- **Notre cible = FastAPI/uvicorn** (concurrent Python direct). Honnêteté : le
  handler reste du Python (GIL), on ne battra pas Encore.ts ; mais le cycle
  HTTP, le routing, la validation query/headers et le streaming tournent en Rust
  pendant que le handler relâche le GIL. Angle : montrer où Vignemale gagne
  (concurrence I/O, streaming, requêtes triviales) et rester transparent là où
  c'est à parité (CPU pur dans le handler).

## Idées notées pour plus tard

- Trace varint custom (EventBuffer) plus compacte que protobuf ; mais OTel
  demandé par les clients dès le début → on vise OTel.
- Sampling décidé **au début de la requête**, propagé à tous les spans enfants
  (sinon traces partielles invalides) — à retenir pour notre trace W3C.
- Erreurs structurées (nom topic, taille msg, provider) > `anyhow::Context`
  générique — dette qu'on peut éviter en typant nos erreurs sqldb/api.
