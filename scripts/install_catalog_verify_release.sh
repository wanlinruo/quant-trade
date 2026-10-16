#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <40-character-code-sha>" >&2
    exit 2
fi

repo=$(git rev-parse --show-toplevel)
exec python3 "$repo/scripts/install_component_release.py" \
    --repo "$repo" --sha "$1" --component catalog-verify
