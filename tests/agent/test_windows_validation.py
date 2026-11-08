from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from qsys.agent import windows_validation as w

SID = 'S-1-5-21-1-2-3-500'


def receipt(directory=True):
    return dict(sid=SID, owner=SID, filesystem='NTFS', fixed=True, reparse=False,
        directory=directory, protected=True, rules=[
            dict(sid=sid, mask=0x1F01FF, allow=True, inherit_only=False,
                 inheritance=3 if directory else 0) for sid in (SID, 'S-1-5-18')])


def admin_parent_receipt():
    value = receipt()
    value['owner'] = 'S-1-5-32-544'
    value['admin_member'] = True
    value['rules'][0]['sid'] = 'S-1-5-32-544'
    return value


@pytest.mark.parametrize('change', [
    dict(filesystem='ReFS'), dict(fixed=False), dict(reparse=True),
    dict(owner='S-1-1-0'), dict(protected=False), dict(directory=False),
    dict(sid='S-1-5-18'),
])
def test_private_directory_rejects_unverified_security(change):
    with pytest.raises(ValueError):
        w._assert_private(dict(receipt(), **change), directory=True, protected=True)


@pytest.mark.parametrize('change', [dict(sid='S-1-5-32-545'), dict(mask=0x120089),
    dict(allow=False), dict(inherit_only=True), dict(inheritance=0)])
def test_acl_rejects_users_read_access_or_missing_effective_rights(change):
    value = deepcopy(receipt())
    value['rules'][0].update(change)
    with pytest.raises(ValueError):
        w._assert_private(value, directory=True, protected=True)


def test_exact_private_acl_and_trusted_file_default_owner():
    w._assert_private(receipt(), directory=True, protected=True)
    w._assert_private(dict(receipt(False), owner='S-1-5-32-544', protected=False),
                      directory=False, protected=False)
    value = receipt()
    value['rules'].append(dict(value['rules'][0]))
    with pytest.raises(ValueError):
        w._assert_private(value, directory=True, protected=True)


def test_preparation_is_disabled_before_any_io(monkeypatch):
    monkeypatch.setattr(w, '_powershell', lambda *a: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='disabled'):
        w.prepare_windows_validation_root(Path('unused'), 'unused')
    with pytest.raises(ValueError, match='disabled'):
        w.prepare_windows_validation_parent(Path('unused'))


def test_private_bootstrap_parent_requires_exact_acl_and_elevated_identity():
    w._assert_private_bootstrap_parent(admin_parent_receipt())
    w._assert_private_bootstrap_parent(dict(receipt(), admin_member=False))
    value = admin_parent_receipt()
    value['admin_member'] = False
    with pytest.raises(ValueError, match='elevated'):
        w._assert_private_bootstrap_parent(value)
    value = admin_parent_receipt()
    value['rules'].append(dict(value['rules'][0]))
    with pytest.raises(ValueError, match='exact private'):
        w._assert_private_bootstrap_parent(value)
    value = admin_parent_receipt()
    value['rules'][0]['sid'] = 'S-1-5-32-545'
    with pytest.raises(ValueError, match='bootstrap trustee'):
        w._assert_private_bootstrap_parent(value)


def test_prepares_identity_private_parent_without_repair_or_overwrite(tmp_path, monkeypatch):
    parent = tmp_path / 'qsys-async-validation-parent-reboot'
    calls = []
    monkeypatch.setattr(w, '_local_parent_path', lambda path: None)

    def powershell(path, script, *, timeout):
        calls.append((path, script, timeout))
        if path == parent.parent:
            return admin_parent_receipt()
        if script == w._PROTECT_NEW_ROOT:
            return {'prepared': True}
        return dict(receipt(), admin_member=True)

    monkeypatch.setattr(w, '_powershell', powershell)
    result = w.prepare_windows_validation_parent(parent, enabled=True)
    assert result['sid'] == SID
    assert parent.is_dir() and list(parent.iterdir()) == []
    assert calls == [
        (parent.parent, w._INSPECT, w._COLD_INSPECTION_TIMEOUT),
        (parent, w._PROTECT_NEW_ROOT, w._POWERSHELL_TIMEOUT),
        (parent, w._INSPECT, w._POWERSHELL_TIMEOUT),
    ]
    with pytest.raises(FileExistsError):
        w.prepare_windows_validation_parent(parent, enabled=True)


def test_parent_protection_timeout_is_single_attempt_and_preserves_directory(
        tmp_path, monkeypatch):
    parent = tmp_path / 'qsys-async-validation-parent-timeout'
    calls = []
    monkeypatch.setattr(w, '_local_parent_path', lambda path: None)

    def powershell(path, script, *, timeout):
        calls.append((path, script, timeout))
        if path == parent.parent:
            return admin_parent_receipt()
        raise ValueError('Windows security inspection failed: TimeoutExpired')

    monkeypatch.setattr(w, '_powershell', powershell)
    with pytest.raises(ValueError, match='TimeoutExpired'):
        w.prepare_windows_validation_parent(parent, enabled=True)
    assert calls == [
        (parent.parent, w._INSPECT, w._COLD_INSPECTION_TIMEOUT),
        (parent, w._PROTECT_NEW_ROOT, w._POWERSHELL_TIMEOUT),
    ]
    assert parent.is_dir() and list(parent.iterdir()) == []


def test_preparation_never_repairs_existing_or_untrusted_parent(tmp_path, monkeypatch):
    root = tmp_path / 'qsys-async-validation-existing'
    root.mkdir()
    marker = root / '.qsys-async-validation'
    marker.write_text('original')
    calls = []
    monkeypatch.setattr(w, '_local_path', lambda root: None)
    def inspect(path, script, *, timeout):
        calls.append((path, script, timeout))
        return receipt()
    monkeypatch.setattr(w, '_powershell', inspect)
    with pytest.raises(FileExistsError):
        w.prepare_windows_validation_root(root, 'component-validation:test', enabled=True)
    assert marker.read_text() == 'original'
    assert calls == [(root.parent, w._INSPECT, w._COLD_INSPECTION_TIMEOUT)]
    other = tmp_path / 'qsys-async-validation-other'
    monkeypatch.setattr(w, '_powershell',
        lambda *a, **kw: dict(receipt(), protected=False))
    with pytest.raises(ValueError, match='protected'):
        w.prepare_windows_validation_root(other, 'component-validation:test', enabled=True)
    assert not other.exists()


def test_unknown_file_and_hardlink_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / 'qsys-async-validation-ntfs'
    root.mkdir()
    for name in ('.qsys-async-validation', 'dispatch.sqlite3', 'dispatch.lock'):
        (root / name).touch()
    monkeypatch.setattr(w, '_local_path', lambda root: None)
    monkeypatch.setattr(w, '_powershell',
        lambda path, script, *, timeout: receipt(path == root))
    assert w.validate_windows_validation_root(root)['sid'] == SID
    (root / 'unknown').touch()
    with pytest.raises(ValueError, match='unexpected file'):
        w.validate_windows_validation_root(root)
    (root / 'unknown').unlink()
    (tmp_path / 'outside.lock').hardlink_to(root / 'dispatch.lock')
    with pytest.raises(ValueError, match='hardlink'):
        w.validate_windows_validation_root(root)


def test_powershell_path_is_data_and_errors_are_redacted(monkeypatch):
    import base64
    import subprocess
    import json
    captured = []
    monkeypatch.setenv('SystemRoot', 'C:/Windows')
    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(stdout=json.dumps(receipt()).encode())
    monkeypatch.setattr(w.subprocess, 'run', run)
    path = Path("private';whoami;$x='")
    assert w._powershell(path, w._INSPECT)['sid'] == SID
    code = base64.b64decode(captured[0][0][-1]).decode('utf-16le')
    assert str(path) not in code
    assert captured[0][1]['timeout'] == w._POWERSHELL_TIMEOUT
    def fail(*a, **kw):
        raise subprocess.CalledProcessError(1, a, stderr=b'private account token')
    monkeypatch.setattr(w.subprocess, 'run', fail)
    with pytest.raises(ValueError, match='CalledProcessError') as err:
        w._powershell(path, w._INSPECT)
    assert 'private account' not in str(err.value)


def test_cold_inspection_gets_one_longer_read_only_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(w, '_powershell',
        lambda path, script, *, timeout: calls.append((path, script, timeout)) or receipt())
    path = Path('C:/private')
    assert w._inspect(path, cold_start=True)['sid'] == SID
    assert w._inspect(path)['sid'] == SID
    assert calls == [
        (path, w._INSPECT, w._COLD_INSPECTION_TIMEOUT),
        (path, w._INSPECT, w._POWERSHELL_TIMEOUT),
    ]


def test_cold_inspection_timeout_is_not_retried(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setenv('SystemRoot', 'C:/Windows')
    def timeout(*args, **kwargs):
        calls.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(w.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='TimeoutExpired'):
        w._inspect(Path('C:/private'), cold_start=True)
    assert calls == [w._COLD_INSPECTION_TIMEOUT]


def test_preparation_keeps_mutation_single_attempt_on_short_timeout(tmp_path, monkeypatch):
    root = tmp_path / 'qsys-async-validation-timeout'
    calls = []
    monkeypatch.setattr(w, '_local_path', lambda root: None)
    def powershell(path, script, *, timeout):
        calls.append((path, script, timeout))
        if script == w._PROTECT_NEW_ROOT:
            raise ValueError('Windows security inspection failed: TimeoutExpired')
        return receipt()
    monkeypatch.setattr(w, '_powershell', powershell)
    with pytest.raises(ValueError, match='TimeoutExpired'):
        w.prepare_windows_validation_root(
            root, 'component-validation:test', enabled=True)
    assert calls == [
        (root.parent, w._INSPECT, w._COLD_INSPECTION_TIMEOUT),
        (root, w._PROTECT_NEW_ROOT, w._POWERSHELL_TIMEOUT),
    ]
    assert root.is_dir()
    assert list(root.iterdir()) == []
