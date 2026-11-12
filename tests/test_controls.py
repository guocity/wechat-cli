from types import SimpleNamespace

import pytest

from wechat_cli.backends.macos import controls
from wechat_cli.backends.macos.controls import matching_processes
from wechat_cli.core.errors import AppError


def test_process_identity_resolves_aliases_and_excludes_other_copies(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    expected = real / "Bridge.app/Contents/MacOS/WeChat"
    snapshot = "\n".join(
        [
            f"123 {alias}/Bridge.app/Contents/MacOS/WeChat",
            f"124 {real}/Other.app/Contents/MacOS/WeChat",
            f"125 {real}/Bridge.app/Contents/MacOS/WeChatHelper",
            "126 /Applications/WeChat.app/Contents/MacOS/WeChat",
        ]
    )
    assert matching_processes(snapshot, expected) == ["123"]


def test_dictation_waits_for_native_finish_and_final_draft(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(
        controls,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        ),
    )
    controller = controls.Controls(tmp_path)
    monkeypatch.setattr(controller, "chat_name", lambda *_: "File Transfer")

    def composer(_):
        text = "" if clock[0] < 0.25 else "Hello" if clock[0] < 0.75 else "Hello world"
        return {"chatName": "File Transfer", "fileUiReady": True, "draft": text}

    def native_status():
        return {
            "starts": 8 + int(clock[0] >= 0.25),
            "finishes": 8 + int(clock[0] >= 0.5),
            "active": 0.25 <= clock[0] < 0.5,
        }

    monkeypatch.setattr(controller, "invoke", composer)
    native = SimpleNamespace(dictation_status=native_status)
    assert controller.capture_dictation(native, "filehelper", 5) == {
        "source": "wechat_composer",
        "chatId": "filehelper",
        "text": "Hello world",
    }
    assert clock[0] >= 1.5


@pytest.mark.parametrize(
    ("name", "draft", "code"),
    [
        ("Different chat", "", "native_active_chat_mismatch"),
        ("File Transfer", "Saved", "native_draft_not_empty"),
    ],
)
def test_dictation_refuses_wrong_composer_or_existing_draft(tmp_path, monkeypatch, name, draft, code):
    controller = controls.Controls(tmp_path)
    monkeypatch.setattr(controller, "chat_name", lambda *_: "File Transfer")
    monkeypatch.setattr(
        controller, "invoke", lambda _: {"chatName": name, "fileUiReady": True, "draft": draft}
    )
    with pytest.raises(AppError) as caught:
        controller.capture_dictation(None, "filehelper", 5)
    assert caught.value.code == code
