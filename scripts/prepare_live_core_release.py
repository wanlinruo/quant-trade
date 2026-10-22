"""受控Live首次隔离：准备配置、停旧后复制一致性状态、原子绑定；从不覆盖原库。"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import grp
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import subprocess
import tempfile
import tomllib

from qsys.agent.locking import single_instance
from qsys.live.config import load_deploy_config
from qsys.live import state_binding as binding
if __package__:
    from scripts.verify_live_core_preproduction import state_fingerprint
else:
    from verify_live_core_preproduction import state_fingerprint

CONFIG_ROOT = Path('/etc/qsys/components/live-core')
PRIVATE_CONFIG = CONFIG_ROOT / 'deploy.toml'
PRIVATE_STRATEGY = CONFIG_ROOT / 'strategy.py'
JOURNAL_TABLES = ('live_equity', 'live_fills', 'live_rejects', 'live_recon')


def run(*args):
    return subprocess.run(list(args), check=True, text=True, capture_output=True)


def sudo(*args):
    return run('sudo', '-n', *args)


def _value(value):
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_value(v) for v in value) + ']'
    raise ValueError('unsupported explicit configuration value')


def dump_toml(values):
    lines = []

    def table(path, items):
        lines.append('[' + '.'.join(json.dumps(k) for k in path) + ']')
        for key, value in items.items():
            if not isinstance(value, dict):
                lines.append(json.dumps(key) + ' = ' + _value(value))
        for key, value in items.items():
            if isinstance(value, dict):
                table([*path, key], value)

    for key, value in values.items():
        table([key], value)
    return '\n'.join(lines) + '\n'


def expected_config(repo):
    source = repo / 'config/deploy.local.toml'
    cfg = load_deploy_config(str(source))
    if cfg.safety.mode != 'live' or not cfg.safety.orders_enabled:
        raise ValueError('live/true source posture required')
    strategy = asdict(cfg.strategy)
    strategy['file'] = str(PRIVATE_STRATEGY)
    strategy['workbench'] = str((repo / cfg.strategy.workbench).resolve())
    raw = tomllib.loads(source.read_text())
    values = {name: asdict(getattr(cfg, name)) for name in ('redis', 'streams', 'fees', 'safety', 'risk', 'alerts')}
    values['strategy'] = strategy
    values['agent'] = {'consumer_group': cfg.consumer_group, 'consumer_name': cfg.consumer_name}
    values['tushare'] = raw['tushare']
    return dump_toml(values).encode(), (repo / cfg.strategy.file).resolve().read_bytes()


def _install_bytes(value, target, mode='0640', group='qsys-live-core'):
    with tempfile.NamedTemporaryFile() as source:
        source.write(value)
        source.flush()
        temporary = target.with_name('.' + target.name + '.live-new')
        sudo('install', '-o', 'root', '-g', group, '-m', mode, source.name, str(temporary))
        sudo('mv', '-Tf', str(temporary), str(target))


def prepare(repo):
    if binding.current_binding() is not None:
        raise ValueError('bound production configuration must not be rewritten by preparation')
    try:
        account = pwd.getpwnam('qsys-live-core')
    except KeyError:
        sudo('useradd', '--system', '--user-group', '--home-dir', '/nonexistent',
             '--shell', '/usr/sbin/nologin', '--groups', 'qsys-observers', 'qsys-live-core')
        account = pwd.getpwnam('qsys-live-core')
    groups = set(run('id', '-nG', 'qsys-live-core').stdout.split())
    if groups != {'qsys-live-core', 'qsys-observers'} or account.pw_shell != '/usr/sbin/nologin':
        raise ValueError('unexpected Live runtime identity')
    sudo('install', '-d', '-o', 'root', '-g', 'qsys-live-core', '-m', '0750', str(CONFIG_ROOT))
    sudo('setfacl', '-m', 'u:ubuntu:--x,g:qsys-observers:--x', str(CONFIG_ROOT))
    for directory in (binding.STATE_ROOT, binding.STATE_ROOT / 'var', binding.STATE_ROOT / 'var/live', binding.STATE_ROOT / 'alerts'):
        if directory.is_symlink():
            raise ValueError('private state directories must not be symlinks')
        sudo('install', '-d', '-o', 'qsys-live-core', '-g', 'qsys-live-core', '-m', '0700', str(directory))
        sudo('setfacl', '-m', 'u:ubuntu:rwx,g:qsys-observers:r-x,d:u:ubuntu:rwx,d:u:qsys-live-core:rwx,d:g:qsys-observers:r-x', str(directory))
    config, strategy = expected_config(repo)
    _install_bytes(config, PRIVATE_CONFIG)
    _install_bytes(strategy, PRIVATE_STRATEGY)
    return check(repo)


def check(repo):
    expected, strategy = expected_config(repo)
    for path in (PRIVATE_CONFIG, PRIVATE_STRATEGY):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("private configuration must be a root-owned read-only regular file")
    actual = sudo('-u', 'qsys-live-core', 'cat', str(PRIVATE_CONFIG)).stdout.encode()
    if actual != expected or sudo('-u', 'qsys-live-core', 'sha256sum', str(PRIVATE_STRATEGY)).stdout.split()[0] != hashlib.sha256(strategy).hexdigest():
        raise ValueError('private configuration or strategy differs from source facts')
    return {'private_config_sha256': hashlib.sha256(actual).hexdigest(),
            'private_strategy_sha256': hashlib.sha256(strategy).hexdigest()}


def snapshot(source, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError('refuse existing state destination')
    before = state_fingerprint(source)
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(destination)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
    destination.chmod(0o600)
    if state_fingerprint(destination) != before or state_fingerprint(source) != before:
        raise ValueError('consistent state snapshot differs from stopped source')
    return before


def bind(repo, audit_root):
    info = check(repo)
    state = run('systemctl', 'show', 'qsys-live-core.service', '--property=ActiveState,MainPID').stdout
    properties = dict(line.split('=', 1) for line in state.splitlines() if '=' in line)
    if properties.get('ActiveState') not in {'inactive', 'failed'} or properties.get('MainPID') != '0':
        raise ValueError('Live unit must be fully stopped before state binding')
    audit_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    target = binding.LiveStateBinding(binding.STATE_ROOT)
    with single_instance(repo / 'var/live-core.lock'), single_instance(target.lock):
        if binding.current_binding() is None:
            if target.state.exists() or target.state.is_symlink():
                raise ValueError('unbound candidate state exists; preserve and review before retry')
            seed = audit_root / 'consistent-state.sqlite3'
            fingerprint = snapshot(repo / 'var/live-state.sqlite3', seed)
            sudo('install', '-o', 'qsys-live-core', '-g', 'qsys-live-core', '-m', '0640', str(seed), str(target.state))
            sudo('setfacl', '-m', 'u:ubuntu:rw-,g:qsys-observers:r--', str(target.state))
            if state_fingerprint(target.state) != fingerprint:
                raise ValueError('installed state differs from consistent snapshot')
            # 原文件不存在即未急停，不创造默认标记；存在则按原字节保留。
            original_kill = repo / 'var/KILL'
            if original_kill.is_symlink():
                raise ValueError('legacy kill must not be a symlink')
            if original_kill.exists():
                value = original_kill.read_bytes()
                _install_bytes(value, target.kill)
                sudo('chown', 'qsys-live-core:qsys-live-core', str(target.kill))
                sudo('setfacl', '-m', 'u:ubuntu:rw-,g:qsys-observers:r--', str(target.kill))
            elif target.kill.exists():
                raise ValueError('unbound private kill exists; refuse to discard it')
            # marker是唯一激活点，之前所有消费者仍读原库；原库从不移动/覆盖。
            _install_bytes(binding.BINDING_CONTENT, binding.BINDING_FILE, '0644', 'root')
        else:
            fingerprint = state_fingerprint(target.state)
        sudo('chown', 'qsys-live-core:qsys-live-core', str(target.lock))
        for name in JOURNAL_TABLES:
            directory = Path('/data/qsys/warehouse') / name
            if not directory.is_dir() or directory.is_symlink():
                raise ValueError('expected existing journal table directory')
            sudo('setfacl', '-R', '-m', 'u:qsys-live-core:rwX,g:qsys-observers:r-X', str(directory))
            for child in (directory, *(p for p in directory.rglob('*') if p.is_dir())):
                sudo('setfacl', '-d', '-m', 'u:qsys-live-core:rwx,u:ubuntu:rwx,g:qsys-observers:r-x', str(child))
        result = {**info, 'ok': True, 'state_fingerprint': fingerprint,
                  'original_state_retained': True, 'binding_retained_on_code_compensation': True}
        (audit_root / 'binding.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--bind', action='store_true')
    parser.add_argument('--audit-root', type=Path)
    args = parser.parse_args(argv)
    repo = args.repo.resolve(strict=True)
    if repo != Path('/home/ubuntu/qsys'):
        parser.error('only the fixed production checkout is accepted')
    if args.bind and (args.audit_root is None or args.audit_root.parent != Path('/data/qsys/var-deployments/github')):
        parser.error('binding audit must be a direct child of the production record root')
    print(json.dumps(prepare(repo) if args.prepare_only else check(repo) if args.check_only else bind(repo, args.audit_root)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
