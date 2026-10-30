from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("data_gateway", "deploy/windows/data_gateway.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def command(op, **fields):
    return "qsys-data " + base64.urlsafe_b64encode(json.dumps({"op": op, **fields}).encode()).decode()


@pytest.fixture
def server(tmp_path):
    script = tmp_path / "approved.py"
    script.write_text("print('approved')")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    config = {"protocol": 1, "python": str(Path('/usr/bin/python3')),
              "roots": {name: str(tmp_path / name) for name in gateway.COMPONENTS},
              "scripts": {kind: {"path": str(script), "sha256": digest}
                          for kind in ['minute', 'auction']}}
    return gateway.Gateway(config, "tushare-sync")


def execute(server, op, data=b"", **fields):
    output = io.BytesIO()
    value = server.execute(gateway.request(command(op, **fields)), io.BytesIO(data), output)
    return value, output.getvalue()


@pytest.mark.parametrize("original", ["", "powershell whoami", "internal-sftp", "scp -t C:/qsys",
                                       "qsys-data a;whoami", "qsys-data ../config"])
def test_only_fixed_data_protocol_is_accepted(original):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        gateway.request(original)


@pytest.mark.parametrize("fields", [{"op": "delete"}, {"op": "probe", "component": "daily-archive"},
                                     {"op": "prepare", "job": "../daily-archive/minute_20261021"},
                                     {"op": "prepare", "job": "minute_20260230"}])
def test_unknown_operation_namespace_and_invalid_dates_are_refused(fields):
    with pytest.raises(ValueError):
        gateway.request("qsys-data " + base64.urlsafe_b64encode(json.dumps(fields).encode()).decode())


def test_manifest_cannot_inject_paths_or_unregistered_fields(server):
    execute(server, "prepare", job="minute_20261021")
    for data in [b"symbol,dt\n../../secret,2026-10-21\n",
                 b"symbol,dt,command\n600000.SH,2026-10-21,whoami\n",
                 b"symbol,dt\n600000.SH,2026-02-30\n"]:
        with pytest.raises(ValueError):
            execute(server, "put", data=data, job="minute_20261021", size=len(data),
                    sha256=hashlib.sha256(data).hexdigest())
    assert not (server.root/'minute_20261021/manifest.csv').exists()


def test_upload_is_hash_bound_and_atomic(server):
    execute(server, "prepare", job="minute_20261021")
    data=b"symbol,dt\n600000.SH,2026-10-21\n"
    with pytest.raises(ValueError, match="mismatch"):
        execute(server, "put", data=data, job="minute_20261021", size=len(data), sha256="a"*64)
    execute(server, "put", data=data, job="minute_20261021", size=len(data),
            sha256=hashlib.sha256(data).hexdigest())
    assert (server.root/'minute_20261021/manifest.csv').read_bytes()==data
    assert not list(server.root.rglob('*.tmp'))


def test_run_executes_only_approved_hash_without_shell(server, monkeypatch):
    execute(server, "prepare", job="minute_20261021")
    data=b"symbol,dt\n600000.SH,2026-10-21\n"
    execute(server,"put",data=data,job="minute_20261021",size=len(data),sha256=hashlib.sha256(data).hexdigest())
    observed=[]
    monkeypatch.setattr(gateway.subprocess,"run",lambda args,**kw: observed.append((args,kw)) or subprocess.CompletedProcess(args,0))
    fields=dict(job="minute_20261021",sha256=server.scripts['minute']['sha256'],run_id="sync-1",start="2026-10-21",end="2026-10-21")
    execute(server,"run",**fields)
    assert observed[0][1]['shell'] is False
    assert '--whole-window' in observed[0][0]
    assert '--run-id' in observed[0][0]
    fields['sha256']='f'*64
    with pytest.raises(ValueError,match='approved'):
        execute(server,'run',**fields)
    assert len(observed)==1


def test_get_and_inventory_stay_inside_component_and_preserve_bytes(server):
    execute(server,'prepare',job='minute_20261021')
    file=server.root/'minute_20261021/minutes_00000.parquet';file.write_bytes(b'actual-bytes')
    result,_=execute(server,'list',job='minute_20261021')
    assert result['files']==[{'name':file.name,'size':12,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]
    assert execute(server,'get',job='minute_20261021',file=file.name)[1]==file.read_bytes()
    for name in ['../config.json','C:/secret.json','..\\secret.json']:
        with pytest.raises(ValueError):execute(server,'get',job='minute_20261021',file=name)


def test_symlink_job_is_refused_even_when_target_does_not_exist(server,tmp_path):
    server.root.mkdir()
    (server.root/'minute_20261021').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError,match='reparse'):
        execute(server,'prepare',job='minute_20261021')


def test_downloader_partial_failure_preserves_exit_two(server, monkeypatch, tmp_path):
    execute(server, 'prepare', job='minute_20261021')
    data=b'symbol,dt\n600000.SH,2026-10-21\n'
    execute(server,'put',data=data,job='minute_20261021',size=len(data),sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(gateway.subprocess,'run',lambda args,**kw:subprocess.CompletedProcess(args,2))
    config=tmp_path/'gateway.json';config.write_text('{}')
    monkeypatch.setattr(gateway,'Gateway',lambda *args:server)
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND',command('run',job='minute_20261021',
        sha256=server.scripts['minute']['sha256'],run_id='sync-1',start='2026-10-21',end='2026-10-21'))
    # Binary streams are unused by this operation, but the real main requires their interface.
    monkeypatch.setattr(gateway.sys,'stdin',type('In',(),{'buffer':io.BytesIO()})())
    monkeypatch.setattr(gateway.sys,'stdout',type('Out',(),{'buffer':io.BytesIO()})())
    assert gateway.main(['--config',str(config),'--component','tushare-sync'])==2


def test_minute_ledgers_are_included_without_exposing_manifest(server):
    execute(server,'prepare',job='minute_20261021')
    folder=server.root/'minute_20261021'
    (folder/'done.csv').write_bytes(b'symbol,dt\n')
    (folder/'manifest.csv').write_bytes(b'private-request')
    files,_=execute(server,'list',job='minute_20261021')
    assert [f['name'] for f in files['files']]==['done.csv']
    assert execute(server,'get',job='minute_20261021',file='done.csv')[1]==b'symbol,dt\n'


def test_manifest_dates_cannot_escape_the_bounded_run_window():
    from datetime import date
    with pytest.raises(ValueError,match='outside'):
        gateway.validate_manifest(b'symbol,dt\n600000.SH,2026-10-20\n',
                                  start=date(2026,10,21),end=date(2026,10,21))


def approve_dividends(server):
    server.scripts["dividend"] = dict(server.scripts["minute"])
    server.dividend_root = server.root


def test_dividend_is_opt_in_and_refused_for_daily_archive(server):
    job = "dividend_20261028_sync-1"
    with pytest.raises(ValueError, match="not approved"):
        execute(server, "prepare", job=job)
    approve_dividends(server)
    server.component = "daily-archive"
    with pytest.raises(ValueError, match="not approved"):
        execute(server, "prepare", job=job)
    assert not (server.root / job).exists()


def test_dividend_window_hash_run_and_no_replay(server, monkeypatch):
    approve_dividends(server)
    job = "dividend_20261028_sync-1"
    execute(server, "prepare", job=job)
    data = b"symbol,dt\n600017.SH,2026-10-28\n"
    execute(server, "put", job=job, data=data, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    calls = []
    monkeypatch.setattr(gateway.subprocess, "run", lambda args, **kw:
                        calls.append((args, kw)) or subprocess.CompletedProcess(args, 0))
    req = dict(job=job, sha256=server.scripts["dividend"]["sha256"], run_id="sync-1",
               start="2026-10-28", end="2026-10-28")
    with pytest.raises(ValueError, match="mismatch"):
        execute(server, "run", **{**req, "run_id": "wrong"})
    assert not calls
    execute(server, "run", **req)
    args, kw = calls[0]
    assert "--symbols" in args and "--bundle" in args
    assert args[args.index("--out") + 1] == str(server.root / job / "snapshot")
    assert kw["shell"] is False
    with pytest.raises(FileExistsError):
        execute(server, "run", **req)
    with pytest.raises(FileExistsError):
        execute(server, "prepare", job=job)
    with pytest.raises(ValueError, match="immutable"):
        execute(server, "put", job=job, data=data, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    assert len(calls) == 1


def test_dividend_exposes_only_completed_bundle(server):
    approve_dividends(server)
    job = "dividend_20261028_sync-1"
    execute(server, "prepare", job=job)
    folder = server.root / job
    (folder / "manifest.csv").write_text("private request")
    with pytest.raises(ValueError, match="unavailable"):
        execute(server, "list", job=job)
    bundle = folder / "bundle"
    bundle.mkdir()
    (bundle / "events.parquet").write_bytes(b"actual-bytes")
    (bundle / "result.json").write_text("{}")
    result, _ = execute(server, "list", job=job)
    assert [f["name"] for f in result["files"]] == ["events.parquet", "result.json"]
    assert execute(server, "get", job=job, file="events.parquet")[1] == b"actual-bytes"
    with pytest.raises(ValueError, match="unsupported"):
        execute(server, "get", job=job, file="manifest.json")


def test_dividend_can_use_separate_data_volume_without_moving_existing_jobs(server, tmp_path):
    config = {"protocol": 1, "python": str(server.python),
              "roots": {name: str(tmp_path / name) for name in gateway.COMPONENTS},
              "scripts": {**server.scripts, "dividend": dict(server.scripts["minute"])},
              "dividend_root": str(tmp_path / "data-volume" / "dividends")}
    updated = gateway.Gateway(config, "tushare-sync")
    execute(updated, "prepare", job="dividend_20261028_sync-1")
    assert (tmp_path / "data-volume/dividends/dividend_20261028_sync-1").is_dir()
    assert not updated.root.exists()
    execute(updated, "prepare", job="minute_20261028")
    assert (updated.root / "minute_20261028").is_dir()
    config["dividend_root"] = str(updated.root / "nested")
    with pytest.raises(ValueError, match="disjoint"):
        gateway.Gateway(config, "tushare-sync")


@pytest.mark.parametrize('component', ['daily-archive', 'tushare-sync'])
def test_minute_upgrade_executes_exact_current_or_previous_hash_only(server, tmp_path, monkeypatch, component):
    newer = tmp_path / 'new-minute.py'; newer.write_text("print('new source')")
    digest = hashlib.sha256(newer.read_bytes()).hexdigest()
    config = {'protocol': 1, 'python': str(server.python),
              'roots': {name: str(tmp_path / name) for name in gateway.COMPONENTS},
              'scripts': {**server.scripts, 'minute': {'path': str(newer), 'sha256': digest}},
              'previous_minute': dict(server.scripts['minute'])}
    updated = gateway.Gateway(config, component)
    execute(updated, 'prepare', job='minute_20261021')
    data = b'symbol,dt\n600000.SH,2026-10-21\n'
    execute(updated, 'put', data=data, job='minute_20261021', size=len(data), sha256=hashlib.sha256(data).hexdigest())
    calls=[]
    monkeypatch.setattr(gateway.subprocess, 'run', lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))
    req = dict(job='minute_20261021', run_id='upgrade', start='2026-10-21', end='2026-10-21')
    execute(updated, 'run', sha256=digest, **req)
    execute(updated, 'run', sha256=server.scripts['minute']['sha256'], **req)
    assert calls[0][6] == str(newer) and calls[1][6] == server.scripts['minute']['path']
    with pytest.raises(ValueError, match='approved'):
        execute(updated, 'run', sha256='f'*64, **req)
    assert len(calls)==2
    # Changing an already-approved N-1 file cannot bypass the byte hash gate.
    Path(server.scripts['minute']['path']).write_text('modified after approval')
    with pytest.raises(ValueError, match='approved'):
        execute(updated, 'run', sha256=server.scripts['minute']['sha256'], **req)
    assert len(calls)==2


@pytest.mark.parametrize('previous', [[], {}, {'path': '/tmp/no', 'sha256': 'bad'}, {'path': 'relative', 'sha256': 'a'*64}])
def test_previous_minute_config_remains_exactly_bounded(server, tmp_path, previous):
    config = {'protocol': 1, 'python': str(server.python),
              'roots': {name: str(tmp_path / name) for name in gateway.COMPONENTS},
              'scripts': server.scripts, 'previous_minute': previous}
    with pytest.raises(ValueError):
        gateway.Gateway(config, 'tushare-sync')
