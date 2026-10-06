#!/usr/bin/env bash
# Pull the latest Dots Fam and restart. Your .env and data are untouched.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git pull --ff-only
MODE="${MODE:-none}" ./deploy/install.sh
