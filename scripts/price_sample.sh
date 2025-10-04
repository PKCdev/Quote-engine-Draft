#!/usr/bin/env bash
set -euo pipefail

source .venv/bin/activate
python -m quote_engine.cli price MV_reports --configs configs

