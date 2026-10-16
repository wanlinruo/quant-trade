#!/bin/sh
set -eu

repo=$(git rev-parse --show-toplevel)
cd "$repo"

backup_root=/data/qsys/var-deployments/bootstrap-backups
backup="$backup_root/production-entrypoint-$(date -u +%Y%m%dT%H%M%SZ)"
sudo -n install -d -o ubuntu -g ubuntu -m 0750 "$backup"
for installed in \
    /usr/local/libexec/qsys-production-deploy-entrypoint \
    /usr/local/libexec/qsys-production-deploy-ssh \
    /etc/qsys/production-deployment-entrypoint.json \
    /etc/qsys/component-releases.json
do
    if [ -f "$installed" ]; then
        name=$(basename "$installed")
        cp -p "$installed" "$backup/$name"
    fi
done

sudo -n install -d -o root -g root -m 0755 /usr/local/libexec /etc/qsys
# Install trusted data before the executable that requires it.  Publishing the
# entrypoint last prevents a partial bootstrap from leaving status unusable.
sudo -n install -o root -g root -m 0644 \
    deploy/linux/production-deployment-entrypoint.json \
    /etc/qsys/production-deployment-entrypoint.json
sudo -n install -o root -g root -m 0644 \
    deploy/component-releases.json \
    /etc/qsys/component-releases.json
sudo -n install -o root -g root -m 0755 \
    deploy/linux/qsys-production-deploy-ssh.py \
    /usr/local/libexec/qsys-production-deploy-ssh
sudo -n install -o root -g root -m 0755 \
    deploy/linux/qsys-production-deploy-entrypoint.py \
    /usr/local/libexec/qsys-production-deploy-entrypoint

echo "installed fixed production deployment entrypoint; backup=$backup"
