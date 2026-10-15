import json
import subprocess
import sys

import pytest

from wechat_cli.cli import main, parser


def test_options_before_and_after_command():
    for argv in [
        ["--port", "8788", "--data-dir", "/tmp/a", "chats"],
        ["chats", "--port", "8788", "--data-dir", "/tmp/a"],
    ]:
        args = parser().parse_args(argv)
        assert args.port == 8788 and args.data_dir == "/tmp/a"
    with pytest.raises(SystemExit):
        parser().parse_args(["messages", "x", "--limit", "101"])
    with pytest.raises(SystemExit):
        parser().parse_args(["--port", "0", "chats"])


def test_cli_really_calls_http_api(api, capsys):
    server, _, native = api
    common = ["--data-dir", str(server.directory), "--port", str(server.server_address[1])]
    assert main([*common, "chats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    assert main(["messages", "filehelper", "--limit", "2", *common]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "18446744073709551615"
    assert main(["send", "filehelper", "hello", "--key", "cli-test-key", *common]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["acceptedByClient"] is True
    assert "cli-test-key" in output.err
    assert len([c for c in native.calls if c[0] == "send"]) == 1
    assert main(["send", "filehelper", "hello", "--key", "cli-test-key", *common]) == 0
    capsys.readouterr()
    assert len([c for c in native.calls if c[0] == "send"]) == 1


def test_cli_generic_api_and_error_exit(api, capsys):
    server, _, _ = api
    common = ["--data-dir", str(server.directory), "--port", str(server.server_address[1])]
    assert main(["api", "/api/health", *common]) == 0
    assert json.loads(capsys.readouterr().out)["service"] == "wechat-cli"
    assert main(["api", "/api/missing", *common]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "not_found"
    assert main(["web", "--no-open", *common]) == 0
    assert json.loads(capsys.readouterr().out)["url"].endswith("/")


def test_installed_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "wechat_cli", "--version"], text=True, capture_output=True, check=True
    )
    assert result.stdout.strip() == "0.2.1"
