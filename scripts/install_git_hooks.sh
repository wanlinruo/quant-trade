#!/bin/sh
set -eu

root=$(git rev-parse --show-toplevel)
cd "$root"
git config --local core.hooksPath .githooks
chmod +x .githooks/pre-push
echo "已启用 qsys Git hooks：$(git config --local --get core.hooksPath)"
