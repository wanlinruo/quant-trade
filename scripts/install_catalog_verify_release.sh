#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <40-character-code-sha>" >&2
    exit 2
fi

code_sha=$1
case "$code_sha" in
    *[!0-9a-f]*|'')
        echo "code SHA must contain exactly 40 lowercase hexadecimal characters" >&2
        exit 2
        ;;
esac
if [ "${#code_sha}" -ne 40 ]; then
    echo "code SHA must contain exactly 40 lowercase hexadecimal characters" >&2
    exit 2
fi

release_root=/opt/qsys-data/releases/catalog-verify/$code_sha
executable=$release_root/venv/bin/qsys-catalog-verify
repo=$(git rev-parse --show-toplevel)
dropin_target=/etc/systemd/system/qsys-catalog-verify.service.d/zz-isolated-release.conf
state_root=/data/qsys/catalog-verify-state
baseline=$state_root/frozen_baseline.json
legacy_baseline=${QSYS_CATALOG_LEGACY_BASELINE:-$repo/artifacts/frozen_baseline.json}

if [ ! -x "$executable" ] || [ ! -f "$release_root/release.json" ]; then
    echo "versioned catalog verifier release is incomplete: $release_root" >&2
    exit 1
fi
python3 "$repo/scripts/build_component_release.py" --repo "$repo" verify \
    --sha "$code_sha" --component catalog-verify --release "$release_root" >/dev/null
if [ -n "$(find "$release_root" -xdev ! -type l -perm /022 -print -quit)" ]; then
    echo "catalog verifier release must not be writable by group or other" >&2
    exit 1
fi
if sudo -n systemctl is-active --quiet qsys-catalog-verify.service; then
    echo "refusing to switch catalog verifier while a verification is running" >&2
    exit 1
fi

sudo -n chown -R root:root "$release_root"
sudo -n install -d -o root -g root -m 0755 \
    /opt/qsys-data /opt/qsys-data/releases /opt/qsys-data/releases/catalog-verify \
    /opt/qsys-data/catalog-verify /etc/systemd/system/qsys-catalog-verify.service.d
sudo -n install -d -o ubuntu -g ubuntu -m 0750 "$state_root"
if ! sudo -n test -f "$baseline"; then
    if [ ! -f "$legacy_baseline" ]; then
        echo "refusing to initialize frozen truth without the existing baseline: $legacy_baseline" >&2
        exit 1
    fi
    sudo -n install -o ubuntu -g ubuntu -m 0640 "$legacy_baseline" "$baseline"
fi
temporary_link=/opt/qsys-data/catalog-verify/.current-$code_sha
sudo -n ln -sfn "$release_root" "$temporary_link"
sudo -n mv -Tf "$temporary_link" /opt/qsys-data/catalog-verify/current
printf '%s\n' \
    '[Service]' \
    'ExecStart=' \
    'Environment=QSYS_CATALOG_SOURCE_ROOT=/opt/qsys-data/catalog-verify/current/source' \
    'Environment=QSYS_CATALOG_FROZEN_BASELINE=/data/qsys/catalog-verify-state/frozen_baseline.json' \
    'ExecStart=/usr/bin/flock --exclusive --nonblock /home/ubuntu/qsys/var/warehouse-write.lock /opt/qsys-data/catalog-verify/current/venv/bin/qsys-catalog-verify --warehouse /home/ubuntu/qsys/warehouse --config /home/ubuntu/qsys/config/deploy.local.toml --service-runs /home/ubuntu/qsys/var/service-runs' | \
    sudo -n tee "$dropin_target" >/dev/null
sudo -n chmod 0644 "$dropin_target"
sudo -n chown root:root "$dropin_target"
sudo -n systemctl daemon-reload
echo "activated isolated catalog verifier release: $code_sha"
