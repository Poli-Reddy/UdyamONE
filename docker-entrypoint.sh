#!/bin/sh
set -eu

if [ "${AUTO_MIGRATE:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
