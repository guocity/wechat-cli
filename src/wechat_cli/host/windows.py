"""Windows paths, process launch, byte-range locks and private state permissions."""

import msvcrt
import os
import subprocess
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from wechat_cli.core.errors import AppError


def default_data_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "wechat-cli"


def detached_options():
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}


@contextmanager
def lock_file(path, blocking=True, *, instance=False):
    from wechat_cli.core.config import private_directory
    from wechat_cli.host import instance_error

    private_directory(path.parent)
    with path.open("a+b") as handle:
        private_permissions(path)
        if not path.stat().st_size:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        except OSError:
            if instance:
                raise instance_error() from None
            raise AppError(
                "server_already_running", "A server is already running for this data directory", 409
            ) from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@lru_cache(maxsize=1)
def current_user_sid():
    """Use the account SID across elevated SSH and its limited desktop token."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token, needed, name = wintypes.HANDLE(), wintypes.DWORD(), wintypes.LPWSTR()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise AppError("private_permissions_failed", "Cannot identify the Windows account", 424)
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value < 65536:
            raise AppError("private_permissions_failed", "Cannot read the Windows account SID", 424)
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise AppError("private_permissions_failed", "Cannot read the Windows account SID", 424)
        sid = ctypes.c_void_p.from_buffer(buffer)
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(name)):
            raise AppError("private_permissions_failed", "Cannot format the Windows account SID", 424)
        try:
            return name.value
        finally:
            kernel.LocalFree(name)
    finally:
        kernel.CloseHandle(token)


def private_permissions(path, *, directory=False):
    """Protect state with a user/system/admin DACL; chmod is ineffective on NTFS."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    get_dacl = advapi.GetSecurityDescriptorDacl
    get_dacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    get_dacl.restype = wintypes.BOOL
    apply = advapi.SetNamedSecurityInfoW
    apply.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    apply.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    inherit = "OICI" if directory else ""
    sddl = f"D:P(A;{inherit};FA;;;{current_user_sid()})(A;{inherit};FA;;;SY)(A;{inherit};FA;;;BA)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise AppError("private_permissions_failed", "Cannot create Windows state permissions", 424)
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if (
            not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
            or not present
        ):
            raise AppError("private_permissions_failed", "Cannot read Windows state permissions", 424)
        if apply(str(path), 1, 0x80000004, None, None, dacl, None):
            raise AppError(
                "private_permissions_failed",
                "Cannot restrict Windows state to its user, system and administrators",
                424,
            )
    finally:
        kernel.LocalFree(descriptor)
