"""Keep native dependencies platform-specific and shared imports lightweight."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement

from wechat_cli.core import dependencies
from wechat_cli.core.errors import AppError


@pytest.mark.parametrize(
    "system,version,native",
    [
        ("darwin", "3.11", {"frida", "cryptography", "pillow", "pilk"}),
        ("darwin", "3.15", {"frida", "cryptography", "pillow", "pilk"}),
        ("win32", "3.11", {"frida", "zstandard"}),
        ("win32", "3.13", {"frida", "zstandard"}),
        ("win32", "3.14", {"frida"}),
        ("win32", "3.15", {"frida"}),
        ("linux", "3.11", set()),
    ],
)
def test_dependencies_are_selected_without_extras(system, version, native):
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    environment = {
        **default_environment(),
        "sys_platform": system,
        "python_version": version,
        "python_full_version": version + ".0",
        "extra": "",
    }
    selected = {
        requirement.name.lower()
        for value in project["project"]["dependencies"]
        if (requirement := Requirement(value)).marker is None or requirement.marker.evaluate(environment)
    }
    assert selected == {"fastapi", "uvicorn", "truststore"} | native
    assert not project["project"].get("optional-dependencies")


def test_shared_api_imports_without_media_or_native_modules(tmp_path):
    source = """
import importlib.abc, sys
class BlockNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'frida', 'PIL', 'cryptography', 'pilk', 'zstandard'}:
            raise AssertionError('Unexpected native import: ' + fullname)
sys.meta_path.insert(0, BlockNative())
from pathlib import Path
from wechat_cli.api.app import create_app
from wechat_cli.cli import parser
from wechat_cli.core.media import decode_media
assert decode_media(b'GIF89a' + bytes(10)).startswith(b'GIF89a')
create_app(Path(sys.argv[1]), backend=object(), singleton=False)
assert parser().parse_args(['chats']).command == 'chats'
"""
    subprocess.run([sys.executable, "-c", source, str(tmp_path)], check=True, timeout=15)


def test_incomplete_install_reports_automatic_reinstall(monkeypatch):
    monkeypatch.setattr(dependencies, "find_spec", lambda name: None)
    with pytest.raises(AppError) as error:
        dependencies.require_modules(("frida",))
    assert error.value.code == "platform_dependencies_missing"
    assert "uvx --refresh wechat-cli" in str(error.value)
    assert "[mac]" not in str(error.value)


def test_unsupported_voice_rejected_before_codec(api, monkeypatch):
    from wechat_cli.api import routes

    _, client, native = api
    monkeypatch.setattr(native, "capabilities", lambda: {"voiceSending": False})
    monkeypatch.setattr(routes, "prepare_voice", lambda value: pytest.fail("Must not load a codec"))
    with pytest.raises(AppError) as error:
        client.request(
            "/api/messages/voice",
            method="POST",
            data={"chatId": "filehelper", "format": "wav", "dataBase64": "AA=="},
            key="voice-platform-test",
        )
    assert error.value.code == "native_feature_unavailable"
    assert error.value.status == 424
