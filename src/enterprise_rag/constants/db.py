# Postgres role the application connects as (non-owner, NOBYPASSRLS).
APP_DB_ROLE = "rag_app"

# Transaction-local settings read by the RLS policies. The initial migration
# hardcodes the same names on purpose: migrations must not change when this file does.
TENANT_SETTING = "app.tenant_id"
GROUPS_SETTING = "app.groups"
GROUPS_SEPARATOR = ","

HNSW_EF_SEARCH_SETTING = "hnsw.ef_search"
HNSW_ITERATIVE_SCAN_SETTING = "hnsw.iterative_scan"
HNSW_ITERATIVE_SCAN_MODE = "relaxed_order"

DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 10
