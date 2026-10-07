#!/bin/sh
set -eu

repo=$(git rev-parse --show-toplevel)
cd "$repo"

sudo -n install -d -o root -g root -m 0755 /usr/local/libexec /etc/qsys
sudo -n install -o root -g root -m 0755 \
    deploy/linux/qsys-production-deploy-entrypoint.py \
    /usr/local/libexec/qsys-production-deploy-entrypoint
sudo -n install -o root -g root -m 0755 \
    deploy/linux/qsys-production-deploy-ssh.py \
    /usr/local/libexec/qsys-production-deploy-ssh
sudo -n install -o root -g root -m 0644 \
    deploy/linux/production-deployment-entrypoint.json \
    /etc/qsys/production-deployment-entrypoint.json

echo "installed fixed production deployment entrypoint"
