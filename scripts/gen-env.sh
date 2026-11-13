#!/bin/sh
# Generate a local .env with random secrets (never commit .env). Refuses to overwrite an existing file.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] && { echo ".env already exists; not overwriting"; exit 0; }
rand() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }
kms=$(python3 -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())")
pg=$(rand 24)
umask 077
cat > .env <<EOT
ENVIRONMENT=local
REAL_USERS=false
POSTGRES_PASSWORD=$pg
DATABASE_URL=postgresql+asyncpg://saidsigned:$pg@localhost:5432/saidsigned
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=ss$(rand 9)
S3_SECRET_KEY=$(rand 30)
MINIO_KMS_SECRET_KEY=saidsigned-dev-key:$kms
JWT_SECRET=$(rand 48)
COOKIE_SECURE=false
EOT
echo "wrote .env (mode 600)"
