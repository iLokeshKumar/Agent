#!/bin/bash
set -Eeuo pipefail

: "${POSTGRES_APP_USER:?POSTGRES_APP_USER must be set}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD must be set}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=app_user="$POSTGRES_APP_USER" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=app_db="$POSTGRES_DB" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password';
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
CREATE SCHEMA IF NOT EXISTS public;
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
ALTER ROLE :"app_user" IN DATABASE :"app_db" SET search_path TO public;
SQL
