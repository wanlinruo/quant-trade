"""#599 isolated Windows diagnostic gateway. No real intents/broker interfaces.

Run only inside a private marked fixture. A public HMAC fixture authenticates
codec tests, not production identity. Child exits affect only these new workers.
"""
import argparse
import hashlib
import hmac
import importlib.util
import json
import mmap
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

PROTOCOL = 'qsys.external-queue-diagnostic.v1'
KEY = bytes([17] * 32)  # Deliberately public fixture, never a credential.


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def sign(value):
    return hmac.new(KEY, canonical(value), 'sha256').hexdigest()


def now_ms():
    return int(time.time() * 1000)


def read_config(root):
    config = json.loads((root / 'fixture.json').read_text(encoding='utf-8-sig'))
    if set(config) != {'exercise_id', 'session_id', 'phase', 'messages', 'issued_ms',
                       'expires_ms', 'ledger_root', 'ledger_source_sha256'}:
        raise ValueError('diagnostic configuration fields')
    for key in ('exercise_id', 'session_id'):
        if len(config[key]) != 32 or any(c not in '0123456789abcdef' for c in config[key]):
            raise ValueError('diagnostic binding')
    if config['phase'] not in ('first', 'second') or set(config['messages']) != {'a', 'b', 'c', 'd', 'e'}:
        raise ValueError('diagnostic scenario')
    return config


def ledger(config):
    source = Path(__file__).with_name('qmt-external-dispatch-ledger.py')
    if hashlib.sha256(source.read_bytes()).hexdigest() != config['ledger_source_sha256']:
        raise ValueError('diagnostic ledger source changed')
    spec = importlib.util.spec_from_file_location('diagnostic_ledger', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DiagnosticLedger(config['ledger_root'], config['exercise_id'])


def open_memory(config):
    memory = mmap.mmap(-1, 8192, tagname='Local\\qsys599_queue_' + config['session_id'],
                       access=mmap.ACCESS_WRITE)
    memory.seek(6144)
    expected = b'QSYS599_RECOVERY_ONLY:' + config['session_id'].encode('ascii')
    if memory.read(len(expected)) != expected:
        memory.close()
        raise ValueError('coordinator marker absent; no fallback')
    return memory


def append_event(root, value):
    fd = os.open(root / 'wire-events.jsonl', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(fd, canonical(value) + b'\n')
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(memory, generation, value):
    raw = canonical(value)
    if len(raw) > 2000:
        raise ValueError('diagnostic request bound')
    memory.seek(0)
    memory.write(bytes(4))
    memory.seek(8)
    memory.write(raw)
    memory.seek(4)
    memory.write(len(raw).to_bytes(4, 'little'))
    memory.seek(0)
    memory.write(generation.to_bytes(4, 'little'))


def validate_reply(raw, config, message_id, seq):
    obj = json.loads(raw.decode('ascii'))
    fields = {'protocol', 'run_id', 'request_id', 'seq', 'status', 'reason',
              'accepted_count', 'authorizes_trading', 'mac'}
    if type(obj) is not dict or set(obj) != fields:
        raise ValueError('diagnostic response fields')
    signature = obj.pop('mac')
    if not isinstance(signature, str) or not hmac.compare_digest(signature, sign(obj)):
        raise ValueError('diagnostic response signature')
    if obj['protocol'] != PROTOCOL or obj['run_id'] != config['session_id'] or obj['request_id'] != message_id:
        raise ValueError('stale or mismatched diagnostic response')
    if type(obj['seq']) is not int or obj['seq'] != seq or obj['status'] != 'echo' or obj['reason'] != 'ok':
        raise ValueError('diagnostic response status')
    if obj['authorizes_trading'] is not False or type(obj['accepted_count']) is not int or obj['accepted_count'] < 1:
        raise ValueError('diagnostic response scope')
    return dict(response_sha256=hashlib.sha256(raw).hexdigest(),
                accepted_count=obj['accepted_count'], response_bytes=len(raw))


def wait_reply(memory, config, message_id, seq, generation, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        memory.seek(4096)
        actual = int.from_bytes(memory.read(4), 'little')
        size = int.from_bytes(memory.read(4), 'little')
        if actual != generation:
            time.sleep(0.02)  # External fixture worker only, never a QMT callback.
            continue
        if not 1 <= size <= 2000:
            raise ValueError('diagnostic response size')
        raw = memory.read(size)
        memory.seek(4096)
        if int.from_bytes(memory.read(4), 'little') != generation:
            continue
        return validate_reply(raw, config, message_id, seq)
    raise TimeoutError('diagnostic reply not observed')


def worker(root, name, behavior, seq, generation):
    config = read_config(root)
    store = ledger(config)
    message_id = config['messages'][name]
    row = store.read(message_id)
    if row is None:
        store.prepare(message_id, config['issued_ms'], config['expires_ms'])
    attempt = uuid.uuid4().hex
    if not store.claim(message_id, attempt, config['session_id'], now_ms()):
        row = store.read(message_id)
        return dict(status='blocked', message=name, state=row['state'], published=0,
                    before_expiry=now_ms() < row['expires_ms'])
    memory = None
    stage = 'open_memory'
    try:
        memory = open_memory(config)
        row = store.read(message_id)
        value = dict(protocol=PROTOCOL, run_id=config['session_id'], request_id=message_id,
                     seq=seq, kind='ping', payload='QSYS599_DIAGNOSTIC_ONLY',
                     issued_at_ms=row['issued_ms'], expires_at_ms=row['expires_ms'])
        value['mac'] = sign(value)
        stage = 'publish'
        publish(memory, generation, value)
        append_event(root, dict(event='published', message=name, message_id=message_id,
                               session_id=config['session_id'], generation=generation,
                               request_sha256=hashlib.sha256(canonical(value)).hexdigest()))
        if behavior == 'exit_after_publish':
            os._exit(23)  # Only this new diagnostic worker, never a business PID.
        stage = 'wait_reply'
        receipt = wait_reply(memory, config, message_id, seq, generation,
                             2 if behavior == 'receiver_absent' else 90)
        stage = 'commit_transport_ack'
        store.transport_ack(message_id, attempt, receipt['response_sha256'])
        if behavior == 'exit_after_ack':
            os._exit(23)
        store.complete_diagnostic_echo(message_id, attempt, receipt['response_sha256'])
        return dict(status='diagnostic_echoed', message=name, published=1, **receipt)
    except Exception as exc:
        # No error detail, configuration value, path or response body is emitted.
        print(json.dumps(dict(status='worker_failed', stage=stage,
                              error_type=type(exc).__name__, message=name)))
        raise SystemExit(24)
    finally:
        if memory is not None:
            memory.close()


def run_child(root, name, behavior, seq, generation, expected_exit):
    child = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                            'worker', str(root), '--name', name, '--behavior', behavior,
                            '--seq', str(seq), '--generation', str(generation)],
                           capture_output=True, timeout=110)
    with (root / ('child-' + name + '-' + behavior + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(dict(exit_code=child.returncode, stdout=child.stdout.decode('utf-8', errors='replace'), stderr_bytes=len(child.stderr)), stream)
    if child.returncode != expected_exit:
        raise ValueError('unexpected diagnostic child outcome')
    output = json.loads(child.stdout) if child.stdout else None
    if expected_exit == 24 and (output is None or output['stage'] != 'wait_reply' or output['error_type'] != 'TimeoutError'):
        raise ValueError('expected only receiver-absent timeout')
    return dict(message=name, child_exit=child.returncode, result=output)


def phase(root):
    config = read_config(root)
    results = []
    observations = []
    if config['phase'] == 'first':
        results.append(run_child(root, 'a', 'complete', 1, 1, 0))
        results.append(run_child(root, 'b', 'exit_after_publish', 2, 2, 23))
        memory = open_memory(config)
        try:
            observations.append(dict(message='b', **wait_reply(memory, config, config['messages']['b'], 2, 2, 10)))
        finally:
            memory.close()
        cold = ledger(config)
        if cold.recover_unfinished() != 1 or cold.read(config['messages']['b'])['state'] != 'unknown':
            raise ValueError('post-publish recovery did not preserve unknown')
        results.append(run_child(root, 'b', 'complete', 2, 2, 0))
        if results[-1]['result']['status'] != 'blocked':
            raise ValueError('unknown was resent')
        results.append(run_child(root, 'c', 'exit_after_ack', 3, 3, 23))
        cold = ledger(config)
        if cold.recover_unfinished() != 1 or not cold.read(config['messages']['c'])['transport_ack_sha256']:
            raise ValueError('post-ACK recovery lost evidence')
        results.append(run_child(root, 'c', 'complete', 3, 3, 0))
        results.append(run_child(root, 'e', 'receiver_absent', 4, 4, 24))
        cold = ledger(config)
        if cold.recover_unfinished() != 1:
            raise ValueError('receiver exit recovery failed')
        results.append(run_child(root, 'e', 'complete', 4, 4, 0))
    else:
        cold = ledger(config)
        for name in ('a', 'b', 'c', 'e'):
            row = cold.read(config['messages'][name])
            expected = 'diagnostic_echoed' if name == 'a' else 'unknown'
            if row is None or row['state'] != expected:
                raise ValueError('prior diagnostic state missing')
            results.append(run_child(root, name, 'complete', 1, 1, 0))
            if results[-1]['result']['status'] != 'blocked':
                raise ValueError('new receiver session replayed old message')
        results.append(run_child(root, 'd', 'complete', 1, 1, 0))
    final = ledger(config)
    states = {name: final.read(mid) for name, mid in config['messages'].items()}
    events = [json.loads(line) for line in (root / 'wire-events.jsonl').read_text().splitlines()]
    expected_publications = 4 if config['phase'] == 'first' else 1
    if len(events) != expected_publications:
        raise ValueError('diagnostic publication count mismatch')
    result = dict(status='success', phase=config['phase'], exercise_id=config['exercise_id'],
                  session_id=config['session_id'], at_ms=now_ms(), results=results,
                  observations=observations, states=states, publications=events,
                  public_fixture_key=True, real_trading_authorized=False,
                  account_calls=0, trading_calls=0)
    with (root / 'recovery-result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return dict(status='success', phase=config['phase'], publications=len(events))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['worker', 'phase'])
    parser.add_argument('root', type=Path)
    parser.add_argument('--name', choices=['a', 'b', 'c', 'd', 'e'])
    parser.add_argument('--behavior', choices=['complete', 'exit_after_publish', 'exit_after_ack', 'receiver_absent'])
    parser.add_argument('--seq', type=int)
    parser.add_argument('--generation', type=int)
    args = parser.parse_args()
    try:
        output = phase(args.root) if args.mode == 'phase' else worker(args.root, args.name, args.behavior, args.seq, args.generation)
        print(json.dumps(output))
    except Exception as exc:
        print(json.dumps(dict(status='failed', error_type=type(exc).__name__)))
        return 25
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
