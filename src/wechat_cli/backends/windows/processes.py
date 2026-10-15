"""Main-process identity and interactive desktop launch for the Windows copy."""

import ctypes
import os
import time
import uuid
from pathlib import Path

from wechat_cli.core.errors import AppError
from wechat_cli.host import instance_error

from .setup import powershell


def process_snapshot():
    import re
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD),
            ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD),
            ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260),
        ]

    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", ctypes.c_void_p)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry),
    ]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    native.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    native.NtQueryInformationProcess.restype = wintypes.LONG
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise AppError("process_inspection_failed", "Cannot inspect running WeChat instances", 424)
    processes = []
    try:
        item = ProcessEntry()
        item.size = ctypes.sizeof(item)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(item))
        while present:
            if item.exe.lower() in {"weixin.exe", "wechat.exe"}:
                handle = kernel.OpenProcess(0x1000, False, item.pid)
                path, helper_type = None, None
                if handle:
                    try:
                        buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            path = buffer.value
                        needed = wintypes.ULONG()
                        native.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
                        if 0 < needed.value < 65536:
                            command = ctypes.create_string_buffer(needed.value)
                            if (
                                native.NtQueryInformationProcess(
                                    handle, 60, command, needed.value, ctypes.byref(needed)
                                )
                                == 0
                            ):
                                value = UnicodeString.from_buffer(command)
                                if value.buffer and value.length <= needed.value:
                                    # Read solely to classify Chromium children; never return/log arguments.
                                    text = ctypes.wstring_at(value.buffer, value.length // 2)
                                    match = re.search(r"(?:^|\s)--type=([a-z-]+)(?:\s|$)", text)
                                    helper_type = match.group(1) if match else None
                    finally:
                        kernel.CloseHandle(handle)
                processes.append(
                    {"Id": int(item.pid), "Path": path, "parent": int(item.parent), "type": helper_type}
                )
            present = kernel.Process32NextW(snapshot, ctypes.byref(item))
    finally:
        kernel.CloseHandle(snapshot)
    return processes


HELPER_TYPES = {
    "renderer",
    "gpu-process",
    "utility",
    "crashpad-handler",
    "zygote",
    "wxutility",
    "wxocr",
    "wxpublic",
    "xplayer",
}


def main_processes():
    processes = process_snapshot()
    by_id = {p["Id"]: p for p in processes}
    return [
        p
        for p in processes
        if not (
            p["type"] in HELPER_TYPES
            and p["parent"] in by_id
            and p["Path"]
            and by_id[p["parent"]]["Path"]
            and Path(p["Path"]) == Path(by_id[p["parent"]]["Path"])
        )
    ]


def check_instance(executable, own_pid=None, *, snapshot=None):
    expected = Path(executable).resolve() if executable else None
    own = None
    for process in main_processes() if snapshot is None else snapshot:
        path = process.get("Path")
        if not path or expected is None or Path(path).resolve() != expected or own is not None:
            raise instance_error()
        own = int(process["Id"])
        if own_pid is not None and own != own_pid:
            raise instance_error()
    return own if own_pid is None else own == own_pid


def launch(executable):
    existing = check_instance(executable)
    if existing:
        return existing
    # A task with an InteractiveToken launches in the user's desktop even when
    # the CLI is invoked over SSH (which otherwise puts the GUI in session 0).
    name = "wechat-cli-launch-" + uuid.uuid4().hex
    exe = str(executable).replace("'", "''")
    folder = str(Path(executable).parent).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        f"$a=New-ScheduledTaskAction -Execute '{exe}' -WorkingDirectory '{folder}'; "
        "$p=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited; "
        f"try {{Register-ScheduledTask -TaskName '{name}' -Action $a -Principal $p | Out-Null; "
        f"Start-ScheduledTask -TaskName '{name}'; Start-Sleep -Milliseconds 800}} "
        f"finally {{Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false -ErrorAction SilentlyContinue}}"
    )
    # Normal desktop terminal launches need neither Task Scheduler nor elevation.
    session = ctypes.c_ulong()
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    if session.value:
        import subprocess

        subprocess.Popen([str(executable)], cwd=executable.parent)
    else:
        powershell(script, timeout=30)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pid := check_instance(executable):
            return pid
        time.sleep(0.2)
    raise AppError(
        "wechat_start_timeout",
        "WeChat did not open in the desktop. Sign in to Windows interactively and retry.",
        504,
    )
