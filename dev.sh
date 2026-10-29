#!/bin/sh
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
npm --prefix frontend run build
exec uv run python -m tunetrail.api
