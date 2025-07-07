#!/usr/bin/env bash
# Simple helper to install all dependencies for running tests.
# Run from repository root: ./scripts/setup_env.sh

set -e

if [ -f requirements.txt ]; then
    python -m pip install -r requirements.txt
fi

if [ -f requirements-dev.txt ]; then
    python -m pip install -r requirements-dev.txt
fi
