#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
python -c 'from pathlib import Path; from src.local_setup import prepare_local_files; prepare_local_files(Path.cwd()); print("Senor Agent local configuration is ready.")'
