#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <40-character-code-sha>" >&2
    exit 2
fi
code_sha=$1
case "$code_sha" in *[!0-9a-f]*|'') exit 2;; esac
[ "${#code_sha}" -eq 40 ] || exit 2

release_root=/opt/qsys-research-test/releases/$code_sha
runner=$release_root/venv/bin/qsys-research-runner
repo=$(git rev-parse --show-toplevel)
manifest=$release_root/release.json
[ -d /data/qsys-test/warehouse-snapshot-store/snapshots ] || {
    echo "test snapshot store is not initialized" >&2; exit 1;
}
[ -x "$runner" ] && [ -f "$manifest" ] || {
    echo "test Runner release is incomplete: $release_root" >&2; exit 1;
}
wheel_filename=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wheel_filename"])' "$manifest")
[ "$(basename "$wheel_filename")" = "$wheel_filename" ] && \
    [ -f "$release_root/dist/$wheel_filename" ] || {
    echo "test Runner wheel is invalid: $wheel_filename" >&2; exit 1;
}
python3 "$repo/scripts/build_research_release.py" --repo "$repo" verify \
    --sha "$code_sha" --release "$release_root" >/dev/null
[ -z "$(find "$release_root" -xdev ! -type l -perm /022 -print -quit)" ] || {
    echo "test Runner release must not be writable by group or other" >&2; exit 1;
}

if ! getent group qsys-research-test >/dev/null; then
    sudo -n groupadd --system qsys-research-test
fi
if ! getent passwd qsys-research-test >/dev/null; then
    sudo -n useradd --system --gid qsys-research-test --home-dir /nonexistent \
        --shell /usr/sbin/nologin qsys-research-test
fi
sudo -n chown -R root:root "$release_root"
sudo -n install -d -o root -g qsys-research-test -m 0750 /data/qsys-test/research-jobs
sudo -n install -d -o ubuntu -g qsys-research-test -m 2770 \
    /data/qsys-test/research-jobs/inbox /data/qsys-test/research-jobs/control
sudo -n install -d -o qsys-research-test -g qsys-research-test -m 0750 \
    /data/qsys-test/research-jobs/jobs /data/qsys-test/research-jobs/rejected
sudo -n install -d -o root -g root -m 0755 \
    /opt/qsys-research-test /opt/qsys-research-test/releases /srv/qsys-research-test \
    /etc/qsys
sudo -n install -d -o root -g root -m 0555 /srv/qsys-research-test/snapshots
temporary=/opt/qsys-research-test/.current-$code_sha
sudo -n ln -sfn "$release_root" "$temporary"
sudo -n mv -Tf "$temporary" /opt/qsys-research-test/current
printf 'QSYS_RESEARCH_CODE_SHA=%s\nQSYS_RESEARCH_WHEEL_FILENAME=%s\n' \
    "$code_sha" "$wheel_filename" | \
    sudo -n tee /etc/qsys/research-runner-test.env >/dev/null
sudo -n install -o root -g root -m 0644 \
    "$repo/deploy/systemd/qsys-research-runner-test.service" \
    /etc/systemd/system/qsys-research-runner-test.service
sudo -n systemctl daemon-reload
enable_state=$(sudo -n systemctl is-enabled qsys-research-runner-test.service 2>/dev/null || true)
case "$enable_state" in
    enabled|enabled-runtime|linked|linked-runtime)
        echo "test Runner must never be enabled or linked" >&2
        exit 1
        ;;
esac
echo "staged inactive test Research Runner release: $code_sha"
