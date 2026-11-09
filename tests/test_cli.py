import signal
from unittest.mock import Mock

import pytest

from smtp_proxy import cli
from smtp_proxy.config import save_settings
from test_config import configured


@pytest.fixture
def context(tmp_path, monkeypatch):
    settings = configured()
    save_settings(settings, tmp_path / ".env")
    runtime = Mock()
    factory = Mock(return_value=runtime)
    monkeypatch.setattr(cli, "ProxyRuntime", factory)
    verify = Mock(return_value=tmp_path)
    monkeypatch.setattr(cli, "verify_data_dir", verify)
    monkeypatch.setattr(cli, "default_portable_dir", lambda: None)
    event = Mock()
    monkeypatch.setattr(cli.threading, "Event", Mock(return_value=event))
    return tmp_path, settings, runtime, factory, verify, event


def test_console_lifecycle_without_service(context):
    directory, settings, runtime, factory, verify, event = context
    assert cli.main([]) == 0
    verify.assert_called_once_with()
    factory.assert_called_once_with(settings, directory)
    runtime.start.assert_called_once_with()
    event.wait.assert_called_once_with()
    runtime.stop.assert_called_once_with()


def test_custom_directory_needs_no_service_storage(context):
    directory, _, runtime, _, verify, _ = context
    assert cli.main(["--data-dir", str(directory)]) == 0
    verify.assert_not_called()
    runtime.start.assert_called_once_with()


def test_portable_default_is_shared_directory(context, monkeypatch):
    directory, settings, _, factory, verify, _ = context
    monkeypatch.setattr(cli, "default_portable_dir", lambda: directory)
    assert cli.main([]) == 0
    factory.assert_called_once_with(settings, directory)
    verify.assert_not_called()


def test_explicit_service_mode_uses_protected_directory(context, monkeypatch):
    _, _, _, _, verify, _ = context
    monkeypatch.setattr(cli, "default_portable_dir", lambda: pytest.fail("Unexpected portable lookup"))
    assert cli.main(["--service-mode"]) == 0
    verify.assert_called_once_with()


def test_check_starts_no_runtime(context):
    directory, _, runtime, factory, _, _ = context
    assert cli.main(["--data-dir", str(directory), "--check"]) == 0
    factory.assert_not_called()
    runtime.start.assert_not_called()


def test_missing_configuration_starts_no_runtime(context, capsys):
    directory, _, _, factory, _, _ = context
    (directory / ".env").unlink()
    assert cli.main(["--data-dir", str(directory)]) == 1
    factory.assert_not_called()
    assert "Keine Konfiguration" in capsys.readouterr().err


def test_invalid_configuration_starts_no_runtime(context, capsys):
    directory, _, _, factory, _, _ = context
    (directory / ".env").write_text("UPSTREAM_PASSWORD_B64=invalid!\n")
    assert cli.main(["--data-dir", str(directory)]) == 1
    factory.assert_not_called()
    assert "invalid!" not in capsys.readouterr().err


def test_interrupt_stops_runtime(context):
    _, _, runtime, _, _, event = context
    event.wait.side_effect = KeyboardInterrupt
    assert cli.main([]) == 0
    runtime.stop.assert_called_once_with()


def test_signal_stops_and_restores_handlers(context):
    _, _, runtime, _, _, event = context
    previous = signal.getsignal(signal.SIGINT)

    def wait():
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)

    event.wait.side_effect = wait
    assert cli.main([]) == 0
    event.set.assert_called_once_with()
    assert signal.getsignal(signal.SIGINT) == previous
    runtime.stop.assert_called_once_with()


def test_start_failure_cleans_up_and_redacts(context, capsys):
    _, _, runtime, _, _, _ = context
    runtime.start.side_effect = RuntimeError("credential-secret")
    assert cli.main([]) == 1
    runtime.stop.assert_called_once_with()
    assert "credential-secret" not in capsys.readouterr().err


def test_stop_failure_is_nonzero(context):
    _, _, runtime, _, _, _ = context
    runtime.stop.side_effect = TimeoutError
    assert cli.main([]) == 1