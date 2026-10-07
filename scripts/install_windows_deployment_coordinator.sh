#!/bin/sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
entrypoint=/usr/local/libexec/qsys-windows-deploy-coordinator
config=/etc/qsys/windows-deployment-coordinator.json

sudo -n install -o root -g root -m 0755 \
    "$repo/deploy/linux/qsys-windows-deploy-coordinator.py" "$entrypoint"
if [ ! -e "$config" ]; then
    sudo -n install -o root -g root -m 0600 \
        "$repo/deploy/linux/windows-deployment-coordinator.example.json" "$config"
fi

"$entrypoint" --help >/dev/null
printf 'installed_entrypoint=%s\nconfig=%s\n' "$entrypoint" "$config"
