#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ $# -ne 0 ]]; then
  echo 'Usage: bash build.sh' >&2
  exit 1
fi
exec python3 "$ROOT_DIR/scripts/package_skill.py"
