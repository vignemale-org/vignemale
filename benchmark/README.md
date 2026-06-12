# Benchmark — Vignemale vs FastAPI

Vignemale exécute le cycle HTTP, le routing et la (dé)sérialisation **en Rust
(axum/tokio, multi-thread)** ; le handler Python ne tient le GIL que pour la
logique métier, et le relâche pendant l'I/O. FastAPI exécute tout en Python sur
la boucle asyncio. À configuration égale, l'écart est net.

## Méthodologie (honnête)

- Outil : [`oha`](https://github.com/hatoo/oha), 50 connexions concurrentes, 10 s.
- Apps **équivalentes** : `app_vignemale.py` et `app_fastapi.py` exposent les
  trois mêmes endpoints (JSON simple, paramètre de chemin, body validé Pydantic).
- Même machine, localhost (le réseau n'est pas le facteur mesuré).
- `./bench.sh` lance les deux serveurs et joue les trois scénarios.
  Variables : `CONN`, `DUR`, `WORKERS` (workers uvicorn).

> Reproduire : `cd .. && source runtimes/python/.venv/bin/activate &&
> CONN=50 DUR=10s WORKERS=1 ./benchmark/bench.sh`

## Résultat — à armes égales (1 process chacun)

C'est la comparaison juste : un process Vignemale vs un worker uvicorn.

| Scénario | Vignemale | FastAPI | Écart |
|---|---|---|---|
| `GET /hello` (JSON) | **42 570** req/s · p99 4.5 ms | 17 239 req/s · p99 8.0 ms | **2.5×** |
| `GET /items/42` (param) | **38 458** req/s · p99 5.2 ms | 13 581 req/s · p99 12.6 ms | **2.8×** |
| `POST /orders` (Pydantic) | **29 254** req/s · p99 7.1 ms | 13 940 req/s · p99 9.0 ms | **2.1×** |

## Résultat — Vignemale 1 process vs uvicorn 4 workers

Même avec **4× plus de process côté FastAPI**, Vignemale tient ou dépasse sur
les lectures (son cycle HTTP est en Rust, pas sur la boucle asyncio) :

| Scénario | Vignemale (1 proc) | FastAPI (4 workers) |
|---|---|---|
| `GET /hello` | **42 980** req/s · p99 4.5 ms | 38 871 req/s · p99 6.1 ms |
| `GET /items/42` | **39 807** req/s · p99 4.9 ms | 35 461 req/s · p99 4.3 ms |
| `POST /orders` (Pydantic) | 29 325 req/s · p99 7.8 ms | **34 682** req/s · p99 3.3 ms |

## Lecture honnête des chiffres

- **Vignemale gagne franchement sur les lectures** : routing et sérialisation
  en Rust, GIL relâché pendant l'I/O → un seul process sature mieux qu'un worker
  Python, et rivalise avec 4 workers uvicorn.
- **Sur le POST validé, FastAPI 4 workers repasse devant** : le travail est
  surtout du CPU Python (parse JSON + validation Pydantic + sérialisation), qui
  tient le GIL ; 4 process battent 1 process malgré la traversée FFI. À 1 worker,
  Vignemale reprend l'avantage (2.1×).
- **Ce qu'on ne prétend PAS** : égaler Encore.ts (121k req/s) — leur handler est
  du JS, pas du Python sous GIL ; et la validation reste en Pydantic (Python),
  là où Encore valide en Rust. Notre angle est « le backend Python le plus
  rapide à configuration égale », pas « le plus rapide tout court ».
- **Piste** : un mode multi-process (`VIGNEMALE_WORKERS`) côté Vignemale
  effacerait l'écart sur le POST — chaque process garde son cycle HTTP Rust.
