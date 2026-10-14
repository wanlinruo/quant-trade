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

release_root=/opt/qsys-research/releases/$code_sha
runner=$release_root/venv/bin/qsys-research-runner
snapshot_store=/data/qsys/warehouse-snapshot-store
job_store=/data/qsys/research-jobs
repo=$(git rev-parse --show-toplevel)

if [ ! -x "$runner" ] || [ ! -f "$release_root/release.json" ]; then
    echo "versioned Runner release is incomplete: $release_root" >&2
    exit 1
fi
wheel_filename=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wheel_filename"])' \
    "$release_root/release.json")
wheel=$release_root/dist/$wheel_filename
if [ "$(basename "$wheel_filename")" != "$wheel_filename" ] || [ ! -f "$wheel" ]; then
    echo "versioned Runner wheel is invalid: $wheel_filename" >&2
    exit 1
fi
if [ ! -d "$snapshot_store/snapshots" ]; then
    echo "snapshot store is not initialized: $snapshot_store" >&2
    exit 1
fi
python3 "$repo/scripts/build_research_release.py" --repo "$repo" verify \
    --sha "$code_sha" --release "$release_root" >/dev/null
if [ -n "$(find "$release_root" -xdev ! -type l -perm /022 -print -quit)" ]; then
    echo "Runner release must not be writable by group or other" >&2
    exit 1
fi
sudo -n chown -R root:root "$release_root"

if ! getent group qsys-research >/dev/null; then
    sudo -n groupadd --system qsys-research
fi
if ! getent passwd qsys-research >/dev/null; then
    sudo -n useradd --system --gid qsys-research --home-dir /nonexistent \
        --shell /usr/sbin/nologin qsys-research
fi

sudo -n install -d -o root -g qsys-research -m 0750 "$job_store"
sudo -n install -d -o ubuntu -g qsys-research -m 2770 \
    "$job_store/inbox" "$job_store/control"
sudo -n install -d -o qsys-research -g qsys-research -m 0750 \
    "$job_store/jobs" "$job_store/rejected"
sudo -n install -d -o root -g root -m 0755 /opt/qsys-research /srv/qsys-research /etc/qsys
sudo -n install -d -o root -g root -m 0555 /srv/qsys-research/snapshots

temporary_link=/opt/qsys-research/.current-$code_sha
sudo -n ln -sfn "$release_root" "$temporary_link"
sudo -n mv -Tf "$temporary_link" /opt/qsys-research/current
printf 'QSYS_RESEARCH_CODE_SHA=%s\nQSYS_RESEARCH_WHEEL_FILENAME=%s\n' \
    "$code_sha" "$wheel_filename" | \
    sudo -n tee /etc/qsys/research-runner.env >/dev/null
sudo -n chmod 0644 /etc/qsys/research-runner.env
sudo -n chown root:root /etc/qsys/research-runner.env

sudo -n systemctl daemon-reload
if sudo -n systemctl is-active --quiet qsys-research-runner.service; then
    echo "staged isolated Research Runner release for the next approved restart: $code_sha"
else
    echo "staged inactive isolated Research Runner release: $code_sha"
fi
