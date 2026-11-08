import ast
from pathlib import Path


REFERENCE = Path(__file__).resolve().parents[2] / 'docs' / 'reference'
DRIVER = REFERENCE / 'qmt-windows-reboot-validation-driver.py'
COORDINATOR = REFERENCE / 'qmt-windows-reboot-coordinator.ps1'


def test_reboot_driver_uses_shared_schedule_marker_before_send_gate():
    source = DRIVER.read_text(encoding='utf-8')
    ast.parse(source)

    ready = source.index("write('first-ready.json'")
    wait = source.index('port.wait_scheduled(timeout=180)')
    scheduled = source.index("write('model-scheduled.json'")
    gate = source.index("audit / 'first-go.json'")
    dispatch = source.index('dispatcher.dispatch(command)')

    assert ready < wait < scheduled < gate < dispatch
    assert "evidence='run-bound-shared-memory-marker'" in source
    assert "model_scheduled_sha256" in source


def test_reboot_driver_has_no_log_ui_or_trading_fallback():
    source = DRIVER.read_text(encoding='utf-8')

    for forbidden in (
        'FormulaOutput', 'userdata_mini', 'userdata\\log', '.Responding',
        'MainWindowHandle', 'passorder', 'get_trade_detail_data',
        'cancel_order', 'first-go.json\').open(\'x\'',
    ):
        assert forbidden not in source
    assert "raise TimeoutError('No coordinator send gate; not sent')" in source


def test_reboot_driver_never_zeroes_or_rewrites_coordinator_mapping():
    source = DRIVER.read_text(encoding='utf-8')

    assert 'memory.write(bytes(8192))' not in source
    assert "memory.write(b'QSYS_SYNTHETIC_INTENT_ONLY:'" not in source
    check = source.index("raise ValueError('Coordinator marker absent; not sent')")
    assert check < source.index("write('first-ready.json'")


def test_reboot_driver_records_mode_instead_of_assuming_restart():
    source = DRIVER.read_text(encoding='utf-8')

    assert "mode = fixture['mode']" in source
    assert "if mode not in ('reboot', 'rehearsal'):" in source
    assert "windows_restarted=mode == 'reboot'" in source
    assert "qmt_client_restarted=mode == 'reboot'" in source
    assert 'windows_restarted=True' not in source


def test_coordinator_writes_marker_before_starting_driver():
    source = COORDINATOR.read_text(encoding='ascii')

    mapping = source.index('MemoryMappedFile]::CreateNew(')
    marker = source.index('$view.WriteArray(6144, $marker')
    readback = source.index("throw 'coordinator marker readback mismatch'")
    driver = source.index('$driver.Start()')
    gate = source.index("Write-NewJson 'first-go.json'")
    scheduled = source.index("throw 'scheduled receipt binding mismatch'")

    assert mapping < marker < readback < driver < scheduled < gate


def test_coordinator_binds_mode_specific_boot_and_qmt_identity():
    source = COORDINATOR.read_text(encoding='ascii')

    for required in (
        "$root = $PSScriptRoot",
        "Read-Json 'fixture.json'",
        "$run -cnotmatch '^[0-9a-f]{32}$'",
        "throw 'Windows did not reboot'",
        "throw 'rehearsal boot time changed'",
        "throw 'trusted QMT was not started after reboot'",
        "throw 'rehearsal QMT identity changed'",
        "throw 'reboot not authorized'",
        "throw 'rehearsal not authorized'",
        "throw 'coordinator already started; no replay'",
        "ReadToEndAsync()",
    ):
        assert required in source
    for forbidden in ('passorder', 'FormulaOutput', 'userdata_mini', '.Responding',
                      'MainWindowHandle', 'Restart-Computer', 'shutdown'):
        assert forbidden not in source
