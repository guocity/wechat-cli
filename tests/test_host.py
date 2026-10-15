"""Exercise the real host boundary, including Windows byte-range locks."""

import subprocess
import sys

import pytest

from wechat_cli.core.config import private_write
from wechat_cli.host import lock_file


def test_private_configuration_round_trips_unicode(tmp_path):
    path = tmp_path / "state.json"
    private_write(path, "微信 📨\n")
    assert path.read_bytes() == "微信 📨\n".encode("utf-8")


@pytest.mark.parametrize(
    "instance,expected", [(True, "wechat_instance_conflict"), (False, "server_already_running")]
)
def test_lock_conflict_across_processes_and_release(tmp_path, instance, expected):
    path = tmp_path / "session.lock"
    script = """import sys
from pathlib import Path
from wechat_cli.host import lock_file
from wechat_cli.core.errors import AppError
try:
    with lock_file(Path(sys.argv[1]), blocking=False, instance=sys.argv[2] == 'True'):
        print('acquired')
except AppError as error:
    print(error.code)
"""

    def attempt():
        return subprocess.run(
            [sys.executable, "-c", script, str(path), str(instance)],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    with lock_file(path):
        assert attempt() == expected
    assert attempt() == "acquired"
