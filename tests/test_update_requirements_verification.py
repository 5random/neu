import hashlib

import src.update as update


class _ReqPath:
    def __init__(self, name: str = "requirements.txt", exists: bool = True) -> None:
        self.name = name
        self._exists = exists

    def exists(self) -> bool:
        return self._exists


def test_verify_requirements_file_fails_without_hashes_or_trusted_checksum(monkeypatch) -> None:
    req = _ReqPath()
    messages: list[str] = []
    monkeypatch.setattr(update, "_read_text_file", lambda _: "nicegui\nnumpy\n")

    ok, use_hashes = update._verify_requirements_file(req, messages.append)

    assert ok is False
    assert use_hashes is False
    assert any("every dependency must be hash-pinned" in message for message in messages)


def test_verify_requirements_file_accepts_trusted_checksum(monkeypatch) -> None:
    req = _ReqPath()
    content = "nicegui\nnumpy\n"
    messages: list[str] = []
    monkeypatch.setenv("CVD_REQUIREMENTS_SHA256", hashlib.sha256(content.encode("utf-8")).hexdigest())
    monkeypatch.setattr(update, "_read_text_file", lambda _: content)

    ok, use_hashes = update._verify_requirements_file(req, messages.append)

    assert ok is True
    assert use_hashes is False
    assert any("trusted SHA-256 checksum" in message for message in messages)


def test_verify_requirements_file_accepts_hash_pinned_requirements(monkeypatch) -> None:
    req = _ReqPath()
    monkeypatch.setattr(
        update,
        "_read_text_file",
        lambda _: (
            "example==1.0 --hash=sha256:abc123\n"
            "other==2.0 \\\n"
            "    --hash=sha256:def456\n"
        ),
    )
    messages: list[str] = []

    ok, use_hashes = update._verify_requirements_file(req, messages.append)

    assert ok is True
    assert use_hashes is True
    assert any("per-package hashes" in message for message in messages)


def test_verify_requirements_file_rejects_nested_requirements_directives(monkeypatch) -> None:
    req = _ReqPath()
    monkeypatch.setattr(update, "_read_text_file", lambda _: "-r base.txt\n")
    messages: list[str] = []

    ok, use_hashes = update._verify_requirements_file(req, messages.append)

    assert ok is False
    assert use_hashes is False
    assert any("Unsupported nested requirements directive" in message for message in messages)


def test_verify_requirements_file_rejects_editable_installs(monkeypatch) -> None:
    req = _ReqPath()
    monkeypatch.setattr(update, "_read_text_file", lambda _: "-e git+https://malicious.example/repo\n")
    messages: list[str] = []

    ok, use_hashes = update._verify_requirements_file(req, messages.append)

    assert ok is False
    assert use_hashes is False
    assert any("Editable installs not supported with hash verification" in message for message in messages)
