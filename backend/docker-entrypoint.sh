#!/bin/sh
# MM CostIQ API container.
#   serve            (default) copy the built-in sample to /data on first start, then run the API on :8080
#   test [args]      run the pytest suite against a throwaway copy of the built-in sample
#   cli <command>    costiq.cli: refresh | allocate [--period YYYY-MM] | conformance [-v] | migrate | translate <id>
#   saas_invoice | recommendations | llm_gateway [args]   file-drop adapters (see README, "Bringing in real data")
# DuckDB allows one writer: run cli and adapter commands while the API container is stopped.
set -e
SAMPLE=/app/sample/costiq.duckdb
cmd="${1:-serve}"
[ $# -gt 0 ] && shift

case "$cmd" in
  serve)
    case "$COSTIQ_DB_URL" in
      duckdb:///*)
        db="${COSTIQ_DB_URL#duckdb:///}"
        if [ ! -f "$db" ]; then
          echo "First start: copying the Meridian Energy sample database to $db"
          cp "$SAMPLE" "$db"
        fi ;;
    esac
    exec python -m uvicorn costiq.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*' ;;
  test)
    export COSTIQ_DB_URL="duckdb:///$SAMPLE"
    exec python -m pytest -q -p no:cacheprovider tests "$@" ;;
  cli)
    exec python -m costiq.cli "$@" ;;
  saas_invoice|recommendations|llm_gateway)
    exec python -m "costiq.adapters.$cmd" "$@" ;;
  *)
    exec "$cmd" "$@" ;;
esac
