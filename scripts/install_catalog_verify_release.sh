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
wheel=$release_root/dist/qsys.whl
repo=$(git rev-parse --show-toplevel)
dropin_source=$repo/deploy/component-releases/catalog-verify.conf
dropin_target=/etc/systemd/system/qsys-catalog-verify.service.d/zz-isolated-release.conf

if [ ! -x "$executable" ] || [ ! -f "$wheel" ]; then
    echo "versioned catalog verifier release is incomplete: $release_root" >&2
    exit 1
fi
python3 "$repo/scripts/build_component_release.py" --repo "$repo" verify \
    --sha "$code_sha" --component catalog-verify --release "$release_root" >/dev/null
if [ -n "$(find "$release_root" -xdev -perm /022 -print -quit)" ]; then
    echo "catalog verifier release must not be writable by group or other" >&2
    exit 1
fi
if [ ! -f "$dropin_source" ]; then
    echo "catalog verifier systemd drop-in is missing: $dropin_source" >&2
    exit 1
fi
if sudo -n systemctl is-active --quiet qsys-catalog-verify.service; then
    echo "refusing to switch catalog verifier while a verification is running" >&2
    exit 1
fi

sudo -n chown -R root:root "$release_root"
sudo -n install -d -o root -g root -m 0755 \
    /opt/qsys-data /opt/qsys-data/releases /opt/qsys-data/releases/catalog-verify \
    /opt/qsys-data/catalog-verify
temporary_link=/opt/qsys-data/catalog-verify/.current-$code_sha
sudo -n ln -sfn "$release_root" "$temporary_link"
sudo -n mv -Tf "$temporary_link" /opt/qsys-data/catalog-verify/current
sudo -n install -D -o root -g root -m 0644 "$dropin_source" "$dropin_target"
sudo -n systemctl daemon-reload
echo "activated isolated catalog verifier release: $code_sha"
