#!/bin/sh
set -eu
cd "$(dirname "$0")"
export ARIADNE_DATA_DIR="${ARIADNE_DATA_DIR:-$(pwd)/data}"
export OLLAMA_NO_CLOUD=1
exec python3 -m ariadne serve
