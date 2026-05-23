#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

echo "==> Installing control-plane dependencies (Python 3.12)..."
cd apps/control-plane
uv sync --extra dev --python python3.12 --quiet
cd "$CLAUDE_PROJECT_DIR"

echo "==> Installing Python SDK dependencies..."
cd sdks/python
uv sync --extra dev --quiet
cd "$CLAUDE_PROJECT_DIR"

echo "==> Installing JS SDK dependencies..."
cd sdks/js
npm install --silent
cd "$CLAUDE_PROJECT_DIR"

echo "==> Installing caveman skill..."
curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash

echo "==> Session setup complete."
