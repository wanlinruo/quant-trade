#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <40-character-test-commit-sha>" >&2
    exit 2
fi

target_sha=$1
if [ "${#target_sha}" -ne 40 ]; then
    echo "target must be an exact 40-character lowercase commit SHA" >&2
    exit 2
fi
case "$target_sha" in
    *[!0-9a-f]*)
        echo "target must be an exact 40-character lowercase commit SHA" >&2
        exit 2
        ;;
esac

repo=$(git rev-parse --show-toplevel)
cd "$repo"

unit_target=/etc/systemd/system/qsys-web-test.service
unit_backup_dir=
unit_changed=false
unit_was_present=false
cleanup() {
    status=$?
    set +e
    if [ "$status" -ne 0 ] && [ "$unit_changed" = true ] && [ -n "$unit_backup_dir" ]; then
        rollback_ok=true
        if [ "$unit_was_present" = true ]; then
            sudo -n install -o root -g root -m 0644 \
                "$unit_backup_dir/qsys-web-test.service" "$unit_target" \
                || rollback_ok=false
        else
            sudo -n rm -f -- "$unit_target" || rollback_ok=false
        fi
        sudo -n systemctl daemon-reload || rollback_ok=false
        sudo -n systemctl restart qsys-web-test.service || rollback_ok=false
        if [ "$rollback_ok" != true ]; then
            echo "test web unit rollback failed: $unit_target" >&2
        fi
    fi
    if [ -n "$unit_backup_dir" ]; then
        rm -rf -- "$unit_backup_dir"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$(git branch --show-current)" != "test" ]; then
    echo "test deployment checkout must be on branch test" >&2
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
    echo "refusing to deploy from a dirty worktree: $repo" >&2
    exit 1
fi

current_sha=$(git rev-parse HEAD)
if [ "$current_sha" = "$target_sha" ]; then
    echo "test deployment already at target: $target_sha"
    exit 0
fi

git fetch --prune origin test
remote_sha=$(git rev-parse origin/test)
if [ "$remote_sha" != "$target_sha" ]; then
    echo "origin/test is $remote_sha, requested $target_sha" >&2
    exit 1
fi
# `test` is periodically squash-merged and force-aligned with `main`, so the
# currently deployed commit is not guaranteed to be an ancestor of the next
# verified target. The clean-worktree check above and exact origin/test match
# make an exact reset deterministic while retaining fail-closed target checks.
git reset --hard "$target_sha"

uv_bin=${QSYS_UV_BIN:-/home/ubuntu/.local/bin/uv}
# GitHub CI 已用官方 PyPI 配置执行 `uv sync --locked`。节点使用腾讯云镜像，
# `--locked` 会仅因 registry URL 不同而要求重写整份 lock；部署端因此按 CI 已验证的
# 现有 lock 精确安装，且禁止在节点重新解析或修改 lock。
"$uv_bin" sync --frozen
"$uv_bin" run --frozen python scripts/deployment_contract.py validate-config \
    --environment test \
    --config config/deploy.local.toml \
    --fingerprint-key-file /etc/qsys/config-fingerprint.key
"$uv_bin" run --frozen pytest -q tests/scripts/test_scripts_import.py
unit_backup_dir=$(mktemp -d)
if [ -f "$unit_target" ]; then
    cp -- "$unit_target" "$unit_backup_dir/qsys-web-test.service"
    unit_was_present=true
fi
systemd-analyze verify "$repo/deploy/systemd/qsys-web-test.service"
unit_changed=true
sudo -n install -o root -g root -m 0644 \
    "$repo/deploy/systemd/qsys-web-test.service" "$unit_target"
cmp -s "$repo/deploy/systemd/qsys-web-test.service" "$unit_target"
sudo -n systemctl daemon-reload
sudo -n systemctl restart qsys-web-test.service

attempt=0
until curl --fail --silent --show-error http://127.0.0.1:8421/healthz >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 20 ]; then
        sudo -n systemctl status qsys-web-test.service --no-pager >&2 || true
        echo "test health check failed for $target_sha" >&2
        exit 1
    fi
    sleep 1
done

mkdir -p /data/qsys-test/var/deployments
printf '%s test %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$target_sha" \
    >>/data/qsys-test/var/deployments/history.log
unit_changed=false
echo "test deployment ok: $target_sha"
