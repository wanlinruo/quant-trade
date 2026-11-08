import ast
from pathlib import Path


DRIVER = (Path(__file__).resolve().parents[2] / 'docs' / 'reference'
          / 'qmt-windows-reboot-validation-driver.py')


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
