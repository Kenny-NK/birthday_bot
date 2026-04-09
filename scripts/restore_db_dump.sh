#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -ne 1 ]]; then
  echo "Использование: $0 <путь_к_дампу>" >&2
  exit 1
fi

DUMP_PATH="$1"
if [[ ! -f "$DUMP_PATH" ]]; then
  echo "Файл дампа не найден: $DUMP_PATH" >&2
  exit 1
fi

restore_plain_sql() {
  local input_path="$1"
  docker compose exec -T postgres sh -lc \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < "$input_path"
}

restore_custom_dump() {
  local input_path="$1"
  docker compose exec -T postgres sh -lc \
    'pg_restore --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < "$input_path"
}

case "$DUMP_PATH" in
  *.sql)
    restore_plain_sql "$DUMP_PATH"
    ;;
  *.sql.gz)
    gzip -dc "$DUMP_PATH" | docker compose exec -T postgres sh -lc \
      'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    ;;
  *.gz)
    gzip -dc "$DUMP_PATH" | docker compose exec -T postgres sh -lc \
      'pg_restore --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    ;;
  *)
    restore_custom_dump "$DUMP_PATH"
    ;;
esac

echo "Дамп PostgreSQL восстановлен из $DUMP_PATH"
