from wechat_cli.controls import matching_processes


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
