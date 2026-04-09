#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUTPUT_PATH="${1:-backups/birthday_bot.dump}"
mkdir -p "$(dirname "$OUTPUT_PATH")"

docker compose exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-privileges' \
  > "$OUTPUT_PATH"

echo "Дамп PostgreSQL сохранен в $OUTPUT_PATH"
