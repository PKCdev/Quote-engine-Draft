#!/usr/bin/env bash
set -euo pipefail

source .venv/bin/activate
python -m quote_engine.web

