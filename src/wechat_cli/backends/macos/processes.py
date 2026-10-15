"""Identify main WeChat processes; helpers and unrelated processes do not count."""

import subprocess
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.host import instance_error


def main_processes(snapshot):
    result = {}
    for line in snapshot.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        path = Path(parts[1])
        if path.name in {"WeChat", "Weixin", "微信"} and path.parent.name == "MacOS":
            result[int(parts[0])] = path.resolve()
    return result


def process_snapshot():
    try:
        return subprocess.run(
            ["/bin/ps", "-axo", "pid=,comm="], check=True, capture_output=True, text=True, timeout=3
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise AppError(
            "process_check_failed", "Cannot verify that only one WeChat instance is running", 424
        ) from None


def check_instance(executable, pid=None, snapshot=None):
    processes = main_processes(process_snapshot() if snapshot is None else snapshot)
    expected = Path(executable).resolve()
    if len(processes) > 1 or any(path != expected for path in processes.values()):
        raise instance_error()
    if pid is not None:
        return processes.get(pid) == expected
    return next(iter(processes), None)
