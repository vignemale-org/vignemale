#!/usr/bin/env bash
# Benchmark Vignemale vs FastAPI — oha, 3 scenarios. Usage (venv active): ./bench.sh
set -euo pipefail
cd "$(dirname "$0")"

CONN=${CONN:-50}
DUR=${DUR:-10s}
WORKERS=${WORKERS:-4}   # uvicorn workers; vignemale = 1 process (note)

echo "== starting =="
VIGNEMALE_WORKERS=$WORKERS VIGNEMALE_ADDR=127.0.0.1:8080 vignemale run app_vignemale.py --addr 127.0.0.1:8080 >/tmp/vgm.log 2>&1 &
VGM=$!
uvicorn app_fastapi:app --host 127.0.0.1 --port 8081 --workers "$WORKERS" --log-level warning >/tmp/fa.log 2>&1 &
FA=$!
trap 'kill $VGM $FA 2>/dev/null || true' EXIT
for url in http://127.0.0.1:8080/hello http://127.0.0.1:8081/hello; do
  for _ in $(seq 1 60); do curl -sf "$url" >/dev/null 2>&1 && break; sleep 0.2; done
done
echo "  vignemale: $WORKERS worker(s) · fastapi/uvicorn: $WORKERS worker(s) · c=$CONN duration=$DUR"
echo

bench() { oha -c "$CONN" -z "$DUR" --no-tui --output-format json "$@" | python3 _parse.py; }

run() {
  local name=$1 path=$2; shift 2
  local v f
  v=$(bench "$@" "http://127.0.0.1:8080$path")
  f=$(bench "$@" "http://127.0.0.1:8081$path")
  printf "%-24s vignemale %8s req/s  p99 %6s ms\n" "$name" "$(echo "$v"|cut -f1)" "$(echo "$v"|cut -f2)"
  printf "%-24s fastapi   %8s req/s  p99 %6s ms\n" "" "$(echo "$f"|cut -f1)" "$(echo "$f"|cut -f2)"
  echo
}

echo "== results =="
run "GET /hello (JSON)"        "/hello"
run "GET /items/42 (param)"    "/items/42"
run "POST /orders (Pydantic)"  "/orders" -m POST -d '{"item_id":7,"qty":3}' -T application/json
