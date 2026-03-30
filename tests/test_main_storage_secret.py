import logging
from unittest.mock import Mock

import main as app_main


def test_resolve_storage_secret_prefers_env_value(monkeypatch) -> None:
    secret_file = Mock()
    monkeypatch.setenv("CVD_STORAGE_SECRET", "env-secret")

    secret = app_main.resolve_storage_secret(
        logging.getLogger("test.main.storage.env"),
        secret_file=secret_file,
    )

    assert secret == "env-secret"
    secret_file.read_text.assert_not_called()


def test_resolve_storage_secret_reads_persisted_file(monkeypatch) -> None:
    secret_file = Mock()
    secret_file.read_text.return_value = "persisted-secret\n"
    monkeypatch.delenv("CVD_STORAGE_SECRET", raising=False)

    secret = app_main.resolve_storage_secret(
        logging.getLogger("test.main.storage.file"),
        secret_file=secret_file,
    )

    assert secret == "persisted-secret"
    secret_file.read_text.assert_called_once_with(encoding="utf-8")


def test_resolve_storage_secret_generates_and_persists_secret(monkeypatch) -> None:
    secret_file = Mock()
    secret_file.read_text.side_effect = FileNotFoundError
    monkeypatch.delenv("CVD_STORAGE_SECRET", raising=False)
    monkeypatch.setattr(app_main.secrets, "token_urlsafe", lambda _: "generated-secret")
    write_secret_file = Mock()
    monkeypatch.setattr(app_main, "_write_storage_secret_file", write_secret_file)

    secret = app_main.resolve_storage_secret(
        logging.getLogger("test.main.storage.generated"),
        secret_file=secret_file,
    )

    assert secret == "generated-secret"
    secret_file.read_text.assert_called_once_with(encoding="utf-8")
    write_secret_file.assert_called_once_with(secret_file, "generated-secret")


def test_write_storage_secret_file_uses_owner_only_permissions(monkeypatch) -> None:
    secret_file = Mock()
    writer = Mock()
    fdopen_context = Mock()
    fdopen_context.__enter__ = Mock(return_value=writer)
    fdopen_context.__exit__ = Mock(return_value=False)
    open_mock = Mock(return_value=123)
    fdopen_mock = Mock(return_value=fdopen_context)
    chmod_mock = Mock()
    close_mock = Mock()

    monkeypatch.setattr(app_main.os, "open", open_mock)
    monkeypatch.setattr(app_main.os, "fdopen", fdopen_mock)
    monkeypatch.setattr(app_main.os, "chmod", chmod_mock)
    monkeypatch.setattr(app_main.os, "close", close_mock)
    monkeypatch.setattr(app_main.os, "name", "posix", raising=False)

    app_main._write_storage_secret_file(secret_file, "generated-secret")

    expected_flags = app_main.os.O_WRONLY | app_main.os.O_CREAT | app_main.os.O_TRUNC
    open_mock.assert_called_once_with(secret_file, expected_flags, 0o600)
    fdopen_mock.assert_called_once_with(123, "w", encoding="utf-8")
    writer.write.assert_called_once_with("generated-secret")
    chmod_mock.assert_called_once_with(secret_file, 0o600)
    close_mock.assert_not_called()
