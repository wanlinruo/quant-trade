#!/usr/bin/env python3
"""Stage and verify a private bundle for the #601 QMT signal executor v2.

``stage`` writes the reviewed executor (byte-identical default copy and a
literal-bound enabled copy) plus the gateway's private files (fresh HMAC key,
account, run binding).  The account and key come from / go to private files
only and are never printed.  Nothing is overwritten.  ``verify`` re-derives the
enabled copy from the default and the private files and fails on any drift.

Usage:
    python scripts/prepare_qmt_signal_executor.py stage <bundle> --run-id <32hex> --account-file <path> [--market-price-type 5]
    python scripts/prepare_qmt_signal_executor.py verify <bundle>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'qsys' / 'agent' / 'qmt' / 'executor_source.py'
CLIENT_BUILD = '2.1.33.0'
_RUN_ID = re.compile(r'[0-9a-f]{32}')
# 原系统 XtQuantAdapter 的“市价”为最新价（prType 5）；其他取值需新的现场证据与评审。
ALLOWED_MARKET_TYPES = (None, 5)


def _read_account(path: Path) -> str:
    account = path.read_text(encoding='ascii').strip()
    if re.fullmatch(r'[0-9]{6,20}', account) is None:
        raise ValueError('account file must contain only the numeric account id')
    return account


def render_enabled(default: str, *, run_id: str, account: str, key_hex: str,
                   market_price_type: int | None, live_orders: bool = False) -> str:
    replacements = {
        "RUN_ID = ''": f'RUN_ID = {run_id!r}',
        "CLIENT_BUILD = ''": f'CLIENT_BUILD = {CLIENT_BUILD!r}',
        "ACCOUNT_ID = ''": f'ACCOUNT_ID = {account!r}',
        "KEY_HEX = ''": f'KEY_HEX = {key_hex!r}',
        'ENABLED = False': 'ENABLED = True',
        'PRICE_TYPE_MARKET = None': f'PRICE_TYPE_MARKET = {market_price_type!r}',
        'DRY_RUN = True': f'DRY_RUN = {not live_orders!r}',
    }
    lines = default.split('\n')
    for old, new in replacements.items():
        if lines.count(old) != 1:
            raise ValueError(f'reviewed executor must contain exactly one line {old!r}')
        lines[lines.index(old)] = new
    return '\n'.join(lines)


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(payload)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stage(bundle: Path, *, run_id: str, account_file: Path, market_price_type: int | None = None,
          live_orders: bool = False, source: Path = SOURCE) -> dict:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError('bundle must be an existing non-symlink directory')
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError('run id must be 32 lowercase hex characters')
    if market_price_type not in ALLOWED_MARKET_TYPES:
        raise ValueError('market price type must be omitted or 5 (original latest-price semantics)')
    if any(bundle.iterdir()):
        raise FileExistsError('bundle must be empty; nothing is overwritten')
    account = _read_account(account_file)
    default = source.read_bytes()
    key_hex = secrets.token_hex(32)
    enabled = render_enabled(default.decode('utf-8'), run_id=run_id, account=account,
                             key_hex=key_hex, market_price_type=market_price_type,
                             live_orders=live_orders).encode('utf-8')
    private = bundle / 'private'
    private.mkdir(mode=0o700)
    run = json.dumps({'run_id': run_id, 'client_build': CLIENT_BUILD,
                      'market_price_type': market_price_type, 'dry_run': not live_orders},
                     sort_keys=True).encode('ascii')
    _write_exclusive(bundle / 'executor-default.py', default)
    _write_exclusive(bundle / 'executor-enabled.py', enabled)
    _write_exclusive(private / 'key.hex', key_hex.encode('ascii'))
    _write_exclusive(private / 'account.txt', account.encode('ascii'))
    _write_exclusive(private / 'run.json', run)
    return {'status': 'staged', 'run_id': run_id, 'market_price_type': market_price_type,
            'dry_run': not live_orders,
            'executor_default_sha256': _sha(default), 'executor_enabled_sha256': _sha(enabled),
            'run_json_sha256': _sha(run)}


def verify(bundle: Path, source: Path = SOURCE) -> dict:
    names = sorted(p.name for p in bundle.iterdir())
    if names != ['executor-default.py', 'executor-enabled.py', 'private']:
        raise ValueError('unexpected bundle file set')
    private = bundle / 'private'
    if sorted(p.name for p in private.iterdir()) != ['account.txt', 'key.hex', 'run.json']:
        raise ValueError('unexpected private file set')
    default = (bundle / 'executor-default.py').read_bytes()
    if default != source.read_bytes():
        raise ValueError('default executor differs from reviewed source')
    run = json.loads((private / 'run.json').read_text(encoding='ascii'))
    key_hex = (private / 'key.hex').read_text(encoding='ascii')
    if re.fullmatch(r'[0-9a-f]{64}', key_hex) is None or run.get('client_build') != CLIENT_BUILD:
        raise ValueError('private binding malformed')
    expected = render_enabled(default.decode('utf-8'), run_id=run['run_id'],
                              account=_read_account(private / 'account.txt'), key_hex=key_hex,
                              market_price_type=run.get('market_price_type'),
                              live_orders=run.get('dry_run') is False).encode('utf-8')
    enabled = (bundle / 'executor-enabled.py').read_bytes()
    if enabled != expected:
        raise ValueError('enabled executor differs from the literal-bound default')
    if not isinstance(run.get('dry_run'), bool):
        raise ValueError('private binding malformed')
    return {'status': 'verified', 'run_id': run['run_id'], 'dry_run': run['dry_run'],
            'executor_default_sha256': _sha(default), 'executor_enabled_sha256': _sha(enabled)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('stage')
    s.add_argument('bundle', type=Path)
    s.add_argument('--run-id', required=True)
    s.add_argument('--account-file', type=Path, required=True)
    s.add_argument('--market-price-type', type=int, default=None)
    s.add_argument('--live-orders', action='store_true',
                   help='disable DRY_RUN; only for a maintainer-authorized real window')
    v = sub.add_parser('verify')
    v.add_argument('bundle', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'stage':
        receipt = stage(args.bundle, run_id=args.run_id, account_file=args.account_file,
                        market_price_type=args.market_price_type, live_orders=args.live_orders)
    else:
        receipt = verify(args.bundle)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
