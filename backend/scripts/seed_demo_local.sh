#!/bin/bash
# Local dev: convenient idempotent seed command reproducible
# Usage: ./scripts/seed_demo_local.sh
# Requires: postgres, neo4j, minio running or embedded profile
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BACKEND_DIR"
echo "Seeding demo data (idempotent)..."
python scripts/seed_demo.py
echo "Validating..."
python scripts/validate_demo.py
echo "Demo seed complete — evaluator-ready"
