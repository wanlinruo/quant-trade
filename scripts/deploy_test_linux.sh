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
git merge --ff-only "$target_sha"

uv_bin=${QSYS_UV_BIN:-/home/ubuntu/.local/bin/uv}
# GitHub CI 已用官方 PyPI 配置执行 `uv sync --locked`。节点使用腾讯云镜像，
# `--locked` 会仅因 registry URL 不同而要求重写整份 lock；部署端因此按 CI 已验证的
# 现有 lock 精确安装，且禁止在节点重新解析或修改 lock。
"$uv_bin" sync --frozen
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
echo "test deployment ok: $target_sha"
