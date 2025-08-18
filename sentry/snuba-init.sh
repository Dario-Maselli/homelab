#!/usr/bin/env bash
set -euo pipefail

echo "[snuba-init] Running migrations…"

export SNUBA_SETTINGS=${SNUBA_SETTINGS:-docker}

CH_USER="${CLICKHOUSE_USER:-default}"
CH_PASS="${CLICKHOUSE_PASSWORD:-}"
CH_HTTP_PORT="${CLICKHOUSE_HTTP_PORT:-8123}"

# Helpers: ClickHouse over HTTP
ch()  { curl -sS --fail ${CH_USER:+-u} ${CH_USER:+$CH_USER:$CH_PASS} --data-binary @- "http://clickhouse:${CH_HTTP_PORT}/?database=default" >/dev/null; }
chq() { curl -sS --fail ${CH_USER:+-u} ${CH_USER:+$CH_USER:$CH_PASS} --data-binary @- "http://clickhouse:${CH_HTTP_PORT}/?database=default"; }

# Wait for ClickHouse HTTP to reply
for i in {1..60}; do
  if printf "SELECT 1;" | chq >/dev/null 2>&1; then break; fi
  sleep 1
done

# 1) Try normal migrations
if snuba migrations migrate --force; then
  echo "[snuba-init] Migrations completed."
  exit 0
fi

echo "[snuba-init] Migration failed; applying targeted fix for EAP spans numeric attrs MV…"

# 2) Create the MV with explicit alias if the target table exists
if [ "$(printf "SELECT count() FROM system.tables WHERE database=currentDatabase() AND name='spans_num_attrs_local';" | chq || echo 0)" = "1" ]; then
  printf "DROP VIEW IF EXISTS spans_num_attrs_mv;" | ch || true
  cat <<'SQL' | ch || true
CREATE MATERIALIZED VIEW IF NOT EXISTS spans_num_attrs_mv
TO spans_num_attrs_local
(
  organization_id UInt64,
  trace_id UUID,
  project_id UInt64,
  attr_key String,
  attr_value Float64,
  timestamp DateTime CODEC (ZSTD(1)),
  retention_days UInt16,
  duration_ms SimpleAggregateFunction(max, UInt32),
  count SimpleAggregateFunction(sum, UInt64)
)
AS
SELECT
  organization_id,
  project_id,
  trace_id,
  attrs.1 AS attr_key,
  attrs.2 AS attr_value,
  toStartOfDay(_sort_timestamp) AS timestamp,
  retention_days,
  maxSimpleState(duration_ms) AS duration_ms,  -- alias fixes the MV column error
  1 AS count
FROM eap_spans_local
LEFT ARRAY JOIN arrayConcat(
  CAST(attr_num_0,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_1,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_2,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_3,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_4,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_5,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_6,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_7,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_8,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_9,  'Array(Tuple(String, Float64))'),
  CAST(attr_num_10, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_11, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_12, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_13, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_14, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_15, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_16, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_17, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_18, 'Array(Tuple(String, Float64))'),
  CAST(attr_num_19, 'Array(Tuple(String, Float64))')
) AS attrs
GROUP BY
  organization_id,
  project_id,
  trace_id,
  attr_key,
  attr_value,
  timestamp,
  retention_days;
SQL
fi

echo "[snuba-init] Retrying migrations…"
snuba migrations migrate --force
