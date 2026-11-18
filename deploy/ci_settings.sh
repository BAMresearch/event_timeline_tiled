#!/bin/sh
# deploy/.env from .env.example with generated secrets (CI and throwaway stacks).
set -eu
cd "$(dirname "$0")"
umask 077
secret() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
sed -e "s/^GF_ADMIN_PASSWORD=.*/GF_ADMIN_PASSWORD=$(secret)/" \
    -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret)/" \
    -e "s/^TILED_ADMIN_PASSWORD=.*/TILED_ADMIN_PASSWORD=$(secret)/" \
    -e "s/^TILED_SECRET_KEY=.*/TILED_SECRET_KEY=$(secret)/" \
    .env.example > .env
if grep -q '=change-me$' .env; then echo "unreplaced change-me" >&2; exit 1; fi
