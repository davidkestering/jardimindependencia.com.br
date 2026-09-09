#!/bin/sh
# Backup diário do Postgres do condomínio (pg_dump custom) com retenção de 30 dias.
set -eu
cd "$(dirname "$0")"
mkdir -p data/backups
. ./.env
docker exec condominio-db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "data/backups/condominio-$(date +%F).dump"
find data/backups -name 'condominio-*.dump' -mtime +30 -delete
