#!/bin/sh
set -eu

repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
entrypoint=/usr/local/libexec/qsys-windows-deploy-coordinator
config=/etc/qsys/windows-deployment-coordinator.json

sudo -n install -o root -g root -m 0755 \
    "$repo/deploy/linux/qsys-windows-deploy-coordinator.py" "$entrypoint"
if [ ! -e "$config" ]; then
    # This file contains only host/path metadata (the SSH private key remains a
    # separate 0600 file owned by the deployment user).  The forced-command
    # entrypoint runs unprivileged and therefore must be able to read it.
    sudo -n install -o root -g root -m 0644 \
        "$repo/deploy/linux/windows-deployment-coordinator.example.json" "$config"
fi

"$entrypoint" --help >/dev/null
printf 'installed_entrypoint=%s\nconfig=%s\n' "$entrypoint" "$config"
