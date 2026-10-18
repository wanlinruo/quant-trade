from datetime import date
from pathlib import Path
import json
import os

import pytest

from qsys.ops.service_checks_index import publish
from qsys.ops.services import load_runs


def fixture_record(tmp_path):
    root = tmp_path / 'runs'
    source = root / 'service-checks'
    path = source / '2026/10/18/service-checks-180001-123.json'
    path.parent.mkdir(parents=True)
    record = {'service': 'service-checks', 'run_id': path.stem,
              'started_at': '2026-10-18T18:00:01+08:00', 'status': 'skipped'}
    path.write_text(json.dumps(record))
    return root, source, path, record


def execute(source, destination):
    return publish(source, destination, producer_uid=os.getuid(),
                   legacy_owner=(os.getuid(), os.getgid()))


def test_legacy_and_current_readers_see_one_record_without_rewriting_source(tmp_path):
    root, source, path, record = fixture_record(tmp_path)
    original = path.read_bytes()
    assert execute(source, root) == 1
    legacy = list((root / '2026/10/18').glob('*.json'))
    assert [json.loads(f.read_text()) for f in legacy] == [record]
    assert load_runs(root, date(2026, 10, 18), date(2026, 10, 18)) == [record]
    assert execute(source, root) == 0
    assert path.read_bytes() == original
    assert legacy[0].stat().st_mode & 0o777 == 0o644


def test_conflicting_legacy_record_is_never_overwritten(tmp_path):
    root, source, path, _ = fixture_record(tmp_path)
    legacy = root / '2026/10/18' / path.name
    legacy.parent.mkdir(parents=True)
    legacy.write_text('old content must survive')
    with pytest.raises(ValueError, match='overwrite'):
        execute(source, root)
    assert legacy.read_text() == 'old content must survive'
    assert not list(legacy.parent.glob('.*.index-*'))


@pytest.mark.parametrize('where', ['source_file', 'source_directory', 'destination_directory'])
def test_publisher_refuses_symlink_redirection(tmp_path, where):
    root, source, path, _ = fixture_record(tmp_path)
    external = tmp_path / 'external'
    external.mkdir()
    if where == 'source_file':
        path.unlink()
        path.symlink_to(external / 'secret')
    elif where == 'source_directory':
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(external, target_is_directory=True)
    else:
        (root / '2026').symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        execute(source, root)
    assert not list(external.iterdir())


def test_record_identity_must_match_its_owned_filename(tmp_path):
    root, source, path, record = fixture_record(tmp_path)
    record['service'] = 'live-core'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='identity'):
        execute(source, root)
    assert not (root / '2026').exists()


def test_publisher_requires_producer_ownership(tmp_path):
    root, source, _, _ = fixture_record(tmp_path)
    with pytest.raises(ValueError, match='owned'):
        publish(source, root, producer_uid=os.getuid() + 1,
                legacy_owner=(os.getuid(), os.getgid()))
