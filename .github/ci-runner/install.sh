#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: sudo $0 --repository OWNER/REPO --host-address IP [--runner-group ID]" >&2
  exit 2
}

repository=
host_address=
runner_group=1
while (($#)); do
  case "$1" in
    --repository) repository=${2:-}; shift 2 ;;
    --host-address) host_address=${2:-}; shift 2 ;;
    --runner-group) runner_group=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "installer must run as root" >&2; exit 1; }
[[ $repository =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid repository" >&2; exit 1; }
[[ $host_address =~ ^[0-9a-fA-F:.]+$ ]] || { echo "invalid host address" >&2; exit 1; }
[[ $runner_group =~ ^[0-9]+$ ]] || { echo "invalid runner group" >&2; exit 1; }
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "only Linux x64 hosts are supported" >&2
  exit 1
}
clock=$(date +%H%M)
((10#$clock < 1800 || 10#$clock >= 2100)) || {
  echo "refusing to stage runner during the production high-water window" >&2
  exit 1
}
for unit in qsys-tushare-sync.service qsys-catalog-verify.service qsys-daily-archive.service; do
  systemctl is-active --quiet "$unit" && {
    echo "refusing to stage while $unit is active" >&2
    exit 1
  }
done

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
libexec=/usr/local/libexec/qsys-ci-runner
state=/var/lib/qsys-ci-runner
config=/etc/qsys/ci-runner

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl fuse-overlayfs nftables podman slirp4netns uidmap

# Ubuntu package presets enable a root-owned Podman API socket and maintenance
# units. This runner uses only direct rootless CLI invocations, so retaining
# that surface would violate the isolation contract.
rootful_podman_units=(
  podman.socket
  podman.service
  podman-auto-update.timer
  podman-auto-update.service
  podman-restart.service
  podman-clean-transient.service
)
systemctl disable --now "${rootful_podman_units[@]}" 2>/dev/null || true
systemctl mask "${rootful_podman_units[@]}"
if [[ $(systemctl is-active podman.socket 2>/dev/null || true) == inactive ]]; then
  rm -f /run/podman/podman.sock
fi
systemctl reset-failed podman-restart.service 2>/dev/null || true

if ! id qsys-ci >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$state" --shell /usr/sbin/nologin qsys-ci
fi
runner_uid=$(id -u qsys-ci)
runner_gid=$(id -g qsys-ci)
grep -q '^qsys-ci:' /etc/subuid || usermod --add-subuids 200000-265535 qsys-ci
grep -q '^qsys-ci:' /etc/subgid || usermod --add-subgids 200000-265535 qsys-ci
loginctl enable-linger qsys-ci

install -d -o root -g root -m 0755 "$libexec" "$config"
install -d -o qsys-ci -g qsys-ci -m 0700 "$state" "$state/jit"
for file in ci_runner_hook.py job-started.sh job-completed.sh runner_gate.py runner_controller.py; do
  install -o root -g root -m 0755 "$repo_root/.github/ci-runner/$file" "$libexec/$file"
done

sed \
  -e "s|@@REPOSITORY@@|$repository|g" \
  -e "s|@@RUNNER_GROUP@@|$runner_group|g" \
  -e "s|@@RUNNER_UID@@|$runner_uid|g" \
  -e "s|@@RUNNER_GID@@|$runner_gid|g" \
  "$repo_root/.github/ci-runner/runner.env.in" >"$config/runner.env"
chmod 0644 "$config/runner.env"

install -d -o root -g root -m 0755 /etc/nftables.d
sed -e "s|@@HOST_ADDRESS@@|$host_address|g" \
  -e "s|@@RUNNER_UID@@|$runner_uid|g" \
  "$repo_root/.github/ci-runner/qsys-ci-runner.nft.in" >/etc/nftables.d/qsys-ci-runner.nft
chmod 0644 /etc/nftables.d/qsys-ci-runner.nft
grep -q 'nftables.d/\*.nft' /etc/nftables.conf || \
  printf '\ninclude "/etc/nftables.d/*.nft"\n' >>/etc/nftables.conf
nft --check --file /etc/nftables.conf
systemctl enable --now nftables.service
nft --file /etc/nftables.conf

install -o root -g root -m 0644 \
  "$repo_root/.github/ci-runner/qsys-ci-runner.service" \
  /etc/systemd/system/qsys-ci-runner.service
install -o root -g root -m 0644 \
  "$repo_root/.github/ci-runner/qsys-ci-runner.timer" \
  /etc/systemd/system/qsys-ci-runner.timer
install -o root -g root -m 0644 \
  "$repo_root/.github/ci-runner/qsys-ci-quiesce.service" \
  /etc/systemd/system/qsys-ci-quiesce.service
install -o root -g root -m 0644 \
  "$repo_root/.github/ci-runner/qsys-ci-quiesce.timer" \
  /etc/systemd/system/qsys-ci-quiesce.timer
systemctl daemon-reload

# Build as the unprivileged account. The image and the Actions archive are both
# pinned by digest/checksum in Containerfile. Copy only the reviewed runner
# assets into qsys-ci's private state: production and deployment checkouts are
# intentionally not traversable by this account.
install -d -o qsys-ci -g qsys-ci -m 0700 "/run/user/$runner_uid"
build_context=$(mktemp -d --tmpdir="$state" build-context.XXXXXXXX)
cleanup_build_context() {
  rm -rf -- "$build_context"
}
trap cleanup_build_context EXIT
cp -a "$repo_root/.github/ci-runner/." "$build_context/"
chown -R qsys-ci:qsys-ci "$build_context"
run_as_runner() {
  (
    cd "$state"
    exec setpriv --reuid "$runner_uid" --regid "$runner_gid" --init-groups \
      env HOME="$state" USER=qsys-ci LOGNAME=qsys-ci \
      XDG_RUNTIME_DIR="/run/user/$runner_uid" "$@"
  )
}
run_as_runner podman build --pull=always \
  --tag localhost/qsys-ci-runner:2.338.0 \
  --file "$build_context/Containerfile" \
  "$build_context"
run_as_runner podman image inspect --format '{{.Id}}' \
  localhost/qsys-ci-runner:2.338.0 >"$config/image-id"
chmod 0644 "$config/image-id"

systemctl disable --now qsys-ci-runner.timer qsys-ci-runner.service \
  qsys-ci-quiesce.timer 2>/dev/null || true
echo "same-host runner staged but DISABLED"
echo "do not create $config/enabled or start the timer until #137 and #138 pass"
echo "store the repository-scoped token at $config/github-token (root:root 0400) only at activation"
