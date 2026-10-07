#!/bin/sh
set -eu

repo=$(git rev-parse --show-toplevel)
cd "$repo"

sudo -n install -d -o root -g root -m 0755 /usr/local/libexec /etc/qsys
sudo -n install -o root -g root -m 0755 \
    deploy/linux/qsys-deploy-entrypoint.py /usr/local/libexec/qsys-deploy-entrypoint
sudo -n install -o root -g root -m 0644 \
    deploy/linux/deployment-entrypoint.json /etc/qsys/deployment-entrypoint.json

echo "installed fixed deployment entrypoint: /usr/local/libexec/qsys-deploy-entrypoint"
