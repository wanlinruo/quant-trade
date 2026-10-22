"""Bounded retries for workflow GitHub API GETs; never replay mutations."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time

FORBIDDEN = {'-X', '--method', '-f', '--raw-field', '-F', '--field', '--input'}


def read_api(args: list[str], *, run=subprocess.run, sleep=time.sleep) -> bytes:
    for arg in args:
        if (arg in FORBIDDEN or any(arg.startswith(flag + '=') for flag in FORBIDDEN)
                or any(arg.startswith(flag) and arg != flag for flag in ('-X', '-f', '-F'))):
            raise ValueError('read-only API helper refuses method/body/field arguments')
    if not args:
        raise ValueError('API endpoint required')
    for attempt in range(3):
        try:
            result = run(['gh', 'api', '--method', 'GET', *args],
                         capture_output=True, timeout=40, check=False)
        except subprocess.TimeoutExpired:
            result = subprocess.CompletedProcess([], 124, b'', b'timeout')
        error = result.stderr.decode('utf-8', errors='replace')
        invalid_json = False
        if result.returncode == 0:
            if '--jq' not in args and '-q' not in args and not any(a.startswith('--jq=') for a in args):
                try:
                    json.loads(result.stdout)
                except (ValueError, UnicodeError):
                    invalid_json = True
            if not invalid_json:
                return result.stdout
        status = re.search(r'HTTP (\d{3})', error)
        code = int(status[1]) if status else None
        transient = invalid_json or code in {404, 408, 429, 500, 502, 503, 504} or any(
            text in error.lower() for text in ('unexpected end of json', 'unexpected eof',
                'tls handshake timeout', 'connection reset', 'timed out', 'timeout',
                'temporary failure', 'unexpected_eof', 'eof occurred'))
        if not transient or attempt == 2:
            reason = f'HTTP {code}' if code else 'incomplete response' if invalid_json or 'json' in error.lower() else 'client/network error'
            raise RuntimeError(f'GitHub API GET failed after {attempt + 1} attempt(s): {reason}')
        print(f'GitHub API GET transient read failure; retry {attempt + 2}/3', file=sys.stderr)
        sleep((2, 5)[attempt])
    raise AssertionError('unreachable')


def main() -> int:
    try:
        sys.stdout.buffer.write(read_api(sys.argv[1:]))
        return 0
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # 不回显完整gh输出、请求参数、header或token。
        print(str(exc) if not isinstance(exc, subprocess.TimeoutExpired) else 'GitHub API GET timed out', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
