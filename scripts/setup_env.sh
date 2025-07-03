#!/usr/bin/env bash
# Simple helper to install all dependencies for running tests.
# Run from repository root: ./scripts/setup_env.sh

set -e

if [ -f requirements.txt ] && [ -f requirements-dev.txt ]; then
    pip install -r requirements.txt -r requirements-dev.txt
else
    [ -f requirements.txt ] && pip install -r requirements.txt
    [ -f requirements-dev.txt ] && pip install -r requirements-dev.txt
fi
