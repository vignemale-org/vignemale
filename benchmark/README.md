# Benchmark — Vignemale vs FastAPI

Vignemale runs the HTTP cycle, the routing and the (de)serialization **in Rust
(axum/tokio, multi-threaded)**; the Python handler only holds the GIL for the
business logic, and releases it during I/O. FastAPI runs everything in Python on
the asyncio loop. At equal configuration, the gap is clear.

## Methodology (honest)

- Tool: [`oha`](https://github.com/hatoo/oha), 50 concurrent connections, 10 s.
- **Equivalent** apps: `app_vignemale.py` and `app_fastapi.py` expose the same
  three endpoints (simple JSON, path parameter, Pydantic-validated body).
- Same machine, localhost (the network is not the factor being measured).
- `./bench.sh` starts both servers and runs the three scenarios.
  Variables: `CONN`, `DUR`, `WORKERS` (uvicorn workers).

> Reproduce: `cd .. && source runtimes/python/.venv/bin/activate &&
> CONN=50 DUR=10s WORKERS=1 ./benchmark/bench.sh`

## Result — on equal footing (1 process each)

This is the fair comparison: one Vignemale process vs one uvicorn worker.

| Scenario | Vignemale | FastAPI | Gap |
|---|---|---|---|
| `GET /hello` (JSON) | **42,570** req/s · p99 4.5 ms | 17,239 req/s · p99 8.0 ms | **2.5×** |
| `GET /items/42` (param) | **38,458** req/s · p99 5.2 ms | 13,581 req/s · p99 12.6 ms | **2.8×** |
| `POST /orders` (Pydantic) | **29,254** req/s · p99 7.1 ms | 13,940 req/s · p99 9.0 ms | **2.1×** |

## Result — Vignemale 1 process vs uvicorn 4 workers

Even with **4× more processes on the FastAPI side**, Vignemale holds or exceeds on
the reads (its HTTP cycle is in Rust, not on the asyncio loop):

| Scenario | Vignemale (1 proc) | FastAPI (4 workers) |
|---|---|---|
| `GET /hello` | **42,980** req/s · p99 4.5 ms | 38,871 req/s · p99 6.1 ms |
| `GET /items/42` | **39,807** req/s · p99 4.9 ms | 35,461 req/s · p99 4.3 ms |
| `POST /orders` (Pydantic) | 29,325 req/s · p99 7.8 ms | **34,682** req/s · p99 3.3 ms |

## Result — 4 workers each (`VIGNEMALE_WORKERS=4` vs `uvicorn --workers 4`)

Multi-process erases the validated-POST deficit (the GIL no longer serializes):
Vignemale moves **ahead on all three scenarios**.

| Scenario | Vignemale (4w) | FastAPI (4w) |
|---|---|---|
| `GET /hello` | **39,102** req/s | 36,067 req/s |
| `GET /items/42` | **45,171** req/s | 38,808 req/s |
| `POST /orders` (Pydantic) | **40,794** req/s | 33,139 req/s |

(On a laptop, oha + 8 servers + Postgres share the cores, so 4 workers don't
quadruple the throughput — what matters is the ranking at identical config. On a
dedicated prod machine, the gap widens.)

## An honest reading of the numbers

- **Vignemale wins clearly on the reads**: routing and serialization in Rust, GIL
  released during I/O → a single process saturates better than one Python worker,
  and rivals 4 uvicorn workers.
- **On the validated POST, FastAPI 4 workers takes the lead back**: the work is
  mostly Python CPU (JSON parse + Pydantic validation + serialization), which
  holds the GIL; 4 processes beat 1 process despite the FFI crossing. At 1 worker,
  Vignemale takes the advantage back (2.1×).
- **What we do NOT claim**: matching Encore.ts (121k req/s) — their handler is JS,
  not Python under the GIL; and the validation stays in Pydantic (Python), where
  Encore validates in Rust. Our angle is "the fastest Python backend at equal
  configuration", not "the fastest, period".
- **Resolved**: the multi-process mode `VIGNEMALE_WORKERS=N` (fork +
  SO_REUSEPORT, each worker keeps its Rust HTTP cycle and its interpreter) puts
  Vignemale ahead on all scenarios at identical config — see the 4-workers table.
</content>
