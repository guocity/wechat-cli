"""Portable Windows adapter tests; native calls are exercised separately on Windows."""

import hashlib
import json

import pytest

from wechat_cli.backends.windows import processes, setup
from wechat_cli.backends.windows.backend import WindowsBackend, content
from wechat_cli.backends.windows.processes import check_instance
from wechat_cli.core.errors import AppError


def test_windows_process_identity_excludes_other_paths_and_duplicate_instances(tmp_path):
    executable = tmp_path / "Weixin.exe"
    snapshot = [{"Id": 7, "Path": str(executable)}]
    assert check_instance(executable, snapshot=snapshot) == 7
    assert check_instance(executable, 7, snapshot=snapshot)
    with pytest.raises(AppError):
        check_instance(executable, 8, snapshot=snapshot)
    for other in [
        snapshot[0],
        {"Id": 8, "Path": str(tmp_path / "other/Weixin.exe")},
        {"Id": 8, "Path": None},
    ]:
        with pytest.raises(AppError) as error:
            check_instance(executable, snapshot=[*snapshot, other])
        assert error.value.code == "wechat_instance_conflict"


def test_windows_payload_verification_rejects_modified_files(tmp_path, monkeypatch):
    executable = tmp_path / "Weixin.exe"
    executable.write_bytes(b"executable")
    dll = tmp_path / "test/Weixin.dll"
    dll.parent.mkdir()
    dll.write_bytes(b"payload")
    monkeypatch.setattr(
        setup,
        "PROFILE",
        {
            "version": "test",
            "executableSha256": hashlib.sha256(b"executable").hexdigest(),
            "payloadSha256": hashlib.sha256(b"payload").hexdigest(),
        },
    )
    assert setup.check_app(tmp_path)["executable"] == str(executable)
    dll.write_bytes(b"tampered")
    with pytest.raises(AppError) as error:
        setup.check_app(tmp_path)
    assert error.value.code == "unsupported_wechat_version"


def test_windows_state_rejects_other_backend_and_relative_app(tmp_path):
    for value in [{"backend": "macos", "app": str(tmp_path)}, {"backend": "windows", "app": "relative"}]:
        (tmp_path / "native-runtime.json").write_text(json.dumps(value))
        with pytest.raises(AppError) as error:
            setup.load_runtime(tmp_path)
        assert error.value.code == "setup_required"


def test_windows_content_round_trip_and_bad_encoding():
    assert content({"hex": "微信 📨".encode().hex()}) == "微信 📨"
    with pytest.raises(AppError):
        content({"hex": "ff"})


def test_windows_queries_are_bound_and_preserve_large_ids(tmp_path):
    backend = WindowsBackend(tmp_path)
    requests = []

    def request(method, params=None):
        requests.append((method, params))
        if method == "selfId":
            return {"id": "self"}
        if method == "databases":
            return ["message_0.db"]
        sql = params["sql"]
        if "FROM SessionTable" in sql:
            return [{"username": "filehelper", "unread_count": "0", "is_hidden": "0", "last_timestamp": "5"}]
        if "FROM contact" in sql:
            return [{"username": "filehelper", "remark": "", "nick_name": "File Transfer"}]
        if "FROM sqlite_master" in sql:
            assert params["parameters"] == ["table", "Msg_" + hashlib.md5(b"filehelper").hexdigest()]
            return [{"name": params["parameters"][1]}]
        assert params["parameters"] == [2]
        return [
            {
                "local_id": "18446744073709551615",
                "server_id": "18446744073709551614",
                "local_type": "1",
                "create_time": "5",
                "sort_seq": "10",
                "sender": "self",
                "content": {"hex": b"hello".hex()},
            }
        ]

    backend._request = request
    result = backend.messages("filehelper", 2)
    assert result["items"][0]["id"] == "18446744073709551615"
    assert result["items"][0]["content"] == "hello"
    assert result["items"][0]["isOutgoing"] is True
    assert backend.chats()["items"][0]["displayName"] == "File Transfer"
    with pytest.raises(AppError) as error:
        backend.messages("'; DROP TABLE x;--", 1)
    assert error.value.code == "native_chat_not_found"
    assert all("DROP TABLE" not in p["sql"] for method, p in requests if method == "query")


def test_windows_history_merges_shards_in_native_order(tmp_path, monkeypatch):
    backend = WindowsBackend(tmp_path)
    monkeypatch.setattr(backend, "chats", lambda: {"items": [{"id": "filehelper"}]})
    monkeypatch.setattr(backend, "self_id", lambda: "self")
    monkeypatch.setattr(backend, "_request", lambda method: ["message_0.db", "message_1.db"])

    def query(database, sql, parameters=None, maximum=20000):
        if database == "contact.db":
            return []
        if "sqlite_master" in sql:
            return [{"name": parameters[1]}]
        # Imported/backfilled rows can have larger local IDs than newer messages.
        ids_and_sequences = (
            [("900", "10"), ("800", "9")] if database == "message_0.db" else [("2", "12"), ("1", "11")]
        )
        return [
            {
                "local_id": identifier,
                "sort_seq": sequence,
                "server_id": "0",
                "local_type": "1",
                "create_time": "5",
                "sender": "self",
                "content": {"hex": b"hello".hex()},
            }
            for identifier, sequence in ids_and_sequences
        ]

    monkeypatch.setattr(backend, "_query", query)
    result = backend.messages("filehelper", 2)
    assert [item["id"] for item in result["items"]] == ["2", "1"]
    assert result["total"] == 2
    assert all("sort_seq" not in item for item in result["items"])


def test_windows_transcription_uses_scoped_record(tmp_path, monkeypatch):
    backend = WindowsBackend(tmp_path)
    record = {"account": "self", "chatId": "filehelper", "id": "1"}
    monkeypatch.setattr(backend, "voice_record", lambda chat, identifier: record)
    calls = []

    def request(method, params):
        calls.append((method, params))
        return {"status": "pending", "text": None}

    monkeypatch.setattr(backend, "_request", request)
    assert backend.transcription_result("filehelper", "1")["status"] == "pending"
    assert backend.transcribe_voice("filehelper", "1")["status"] == "pending"
    assert calls == [("voiceTranscript", record), ("transcribeVoice", record)]
    assert backend.capabilities()["voiceSending"] == "wechat_native_queue"
    assert backend.capabilities()["voiceToText"] == "wechat_native_task"


def test_windows_owned_helpers_are_not_extra_instances(tmp_path, monkeypatch):
    exe = str(tmp_path / "Weixin.exe")
    main = {"Id": 1, "Path": exe, "parent": 99, "type": None}
    helper = {"Id": 2, "Path": exe, "parent": 1, "type": "wxutility"}
    foreign = {"Id": 3, "Path": exe, "parent": 99, "type": None}
    monkeypatch.setattr(processes, "process_snapshot", lambda: [main, helper])
    assert processes.main_processes() == [main]
    monkeypatch.setattr(processes, "process_snapshot", lambda: [main, helper, foreign])
    assert processes.main_processes() == [main, foreign]


def test_windows_upgrade_publishes_only_after_checks(tmp_path, monkeypatch):
    from wechat_cli.core.config import save_json

    old = {"backend": "windows", "app": str(tmp_path / "old"), "version": "old"}
    save_json(tmp_path / "native-runtime.json", old)
    candidate = {"backend": "windows", "app": str(tmp_path / "new"), "version": setup.PROFILE["version"]}
    calls = []

    def prepare(directory, *, publish):
        assert publish is False
        assert json.loads((directory / "native-runtime.json").read_text()) == old
        return candidate

    monkeypatch.setattr(setup, "setup", prepare)
    monkeypatch.setattr(processes, "check_instance", lambda value: calls.append(value))
    assert setup.ensure_current(tmp_path) == candidate
    assert calls == [None, None]
    assert json.loads((tmp_path / "previous-native-runtime.json").read_text()) == old
    assert json.loads((tmp_path / "native-runtime.json").read_text()) == candidate


def test_windows_failed_upgrade_preserves_registration(tmp_path, monkeypatch):
    from wechat_cli.core.config import save_json

    old = {"backend": "windows", "app": str(tmp_path / "old"), "version": "old"}
    save_json(tmp_path / "native-runtime.json", old)
    monkeypatch.setattr(processes, "check_instance", lambda value: None)
    monkeypatch.setattr(
        setup, "setup", lambda *a, **kw: (_ for _ in ()).throw(AppError("download_failed", "fixture"))
    )
    with pytest.raises(AppError):
        setup.ensure_current(tmp_path)
    assert json.loads((tmp_path / "native-runtime.json").read_text()) == old


def test_windows_launch_reuses_existing_process_without_starting_another(tmp_path, monkeypatch):
    executable = tmp_path / "Weixin.exe"
    monkeypatch.setattr(processes, "check_instance", lambda path: 42)
    monkeypatch.setattr(
        processes, "powershell", lambda *a, **kw: pytest.fail("Must not launch a second vendor process")
    )
    assert processes.launch(executable) == 42


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="NTFS DACL check requires Windows")
def test_windows_private_state_grants_the_user_sid_across_elevation(tmp_path):
    from wechat_cli.core.config import api_token, private_directory
    from wechat_cli.host.windows import current_user_sid

    directory = private_directory(tmp_path / "private")
    api_token(directory)
    literal = str(directory).replace("'", "''")
    report = json.loads(
        setup.powershell(
            f"@('{literal}','{literal}\\api-token') | ForEach-Object {{"
            "$a=Get-Acl -LiteralPath $_; "
            "[pscustomobject]@{protected=$a.AreAccessRulesProtected; "
            "sids=@($a.Access | ForEach-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value})}"
            "} | ConvertTo-Json -Depth 4"
        )
    )
    for acl in report:
        assert acl["protected"] is True
        assert set(acl["sids"]) == {current_user_sid(), "S-1-5-18", "S-1-5-32-544"}


def test_windows_failed_download_removes_partial_file(tmp_path, monkeypatch):
    destination = tmp_path / "installer.exe"
    partial = destination.with_suffix(".part")
    partial.write_bytes(b"incomplete")
    monkeypatch.setattr(setup, "ssl_context", lambda: None)

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(setup.urllib.request, "urlopen", offline)
    with pytest.raises(AppError) as error:
        setup.download("https://example.invalid/installer.exe", destination)
    assert error.value.code == "download_failed"
    assert not partial.exists()
    assert not destination.exists()


def test_windows_reset_permissions_calls_icacls(tmp_path, monkeypatch):
    calls = []

    def mock_run(cmd, **kwargs):
        calls.append(cmd)
        return None

    monkeypatch.setattr(setup.platform, "system", lambda: "Windows")
    monkeypatch.setattr(setup.subprocess, "run", mock_run)
    setup.reset_permissions(tmp_path)
    assert calls == [["icacls.exe", str(tmp_path), "/reset", "/t"]]
