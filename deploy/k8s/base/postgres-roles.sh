#!/bin/sh
# Runs once, on an empty data volume. Migrations create these roles only IF NOT EXISTS and never
# set passwords, so the passwords live here, in the Secret, and not in git or in migrations.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$RAG_APP_PASSWORD" -v relay_pw="$RAG_RELAY_PASSWORD" <<'SQL'
CREATE ROLE rag_app LOGIN NOBYPASSRLS PASSWORD :'app_pw';
CREATE ROLE rag_relay LOGIN NOBYPASSRLS PASSWORD :'relay_pw';
SQL
