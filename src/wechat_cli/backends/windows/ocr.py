"""One-shot local OCR over Tencent's exported MMMojo C interface.

Runs in a disposable process: the DLL's IPC threads and C callback lifetimes
end together. Wire field numbers follow WeChat 4.x's OCR protocol.
"""

import ctypes as ct
import json
import os
import sys
import threading
from pathlib import Path

from .protobuf import field, fields


def recognize(app, image):
    runtime = app / "4.1.15.53"
    plugins = Path(os.environ["APPDATA"]) / "Tencent/xwechat/xplugin/plugins/WeChatOcr"
    candidates = [p / "extracted" for p in plugins.iterdir() if p.name.isdecimal()]
    plugin = next(
        (
            p
            for p in sorted(candidates, key=lambda p: int(p.parent.name), reverse=True)
            if (p / "wxocr.dll").is_file()
        ),
        None,
    )
    if plugin is None:
        raise RuntimeError("native_ocr_plugin_missing")
    dll_directory = os.add_dll_directory(str(runtime))
    lib = ct.CDLL(str(runtime / "mmmojo_64.dll"))
    pointer, uint = ct.c_void_p, ct.c_uint32

    def bind(name, result, arguments):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, arguments
        return function

    initialize = bind("InitializeMMMojo", None, [ct.c_int, pointer])
    create = bind("CreateMMMojoEnvironment", pointer, [])
    callback = bind("SetMMMojoEnvironmentCallbacks", None, [pointer, ct.c_int, pointer])
    # These two exports are variadic; Win64 passes either trailing value in R8.
    params = bind("SetMMMojoEnvironmentInitParams", None, [pointer, ct.c_int, pointer])
    switch = bind("AppendMMSubProcessSwitchNative", None, [pointer, ct.c_char_p, ct.c_wchar_p])
    start = bind("StartMMMojoEnvironment", None, [pointer])
    stop = bind("StopMMMojoEnvironment", None, [pointer])
    read = bind("GetMMMojoReadInfoRequest", pointer, [pointer, ct.POINTER(uint)])
    remove_read = bind("RemoveMMMojoReadInfo", None, [pointer])
    create_write = bind("CreateMMMojoWriteInfo", pointer, [ct.c_int, ct.c_bool, uint])
    write_buffer = bind("GetMMMojoWriteInfoRequest", pointer, [pointer, ct.c_size_t])
    send = bind("SendMMMojoWriteInfo", ct.c_bool, [pointer, pointer])
    remove_write = bind("RemoveMMMojoWriteInfo", None, [pointer])
    ready, completed = threading.Event(), threading.Event()
    response, errors = [], []

    @ct.CFUNCTYPE(None, uint, pointer, pointer)
    def push(request, info, user):
        try:
            size = uint()
            data_pointer = read(info, ct.byref(size))
            if not data_pointer or not 0 < size.value <= 8 * 1024**2:
                raise ValueError("invalid OCR response size")
            data = dict(fields(ct.string_at(data_pointer, size.value)))
            if request == 10001:
                if data.get(1) != 1:
                    raise ValueError("OCR unsupported")
                ready.set()
            elif request == 10011 and data.get(1) == 2:
                response.append(data)
                completed.set()
        except (ValueError, OSError) as error:
            errors.append(str(error))
            ready.set()
            completed.set()
        finally:
            remove_read(info)

    @ct.CFUNCTYPE(None, ct.c_bool, pointer)
    def connected(value, user):
        if not value:
            errors.append("disconnected")
            ready.set()
            completed.set()

    @ct.CFUNCTYPE(None, pointer)
    def disconnected(user):
        connected(False, user)

    @ct.CFUNCTYPE(None, pointer)
    def launched(user):
        pass

    @ct.CFUNCTYPE(None, ct.c_int, pointer)
    def launch_failed(code, user):
        connected(False, user)

    @ct.CFUNCTYPE(None, pointer, ct.c_int, pointer)
    def mojo_error(data, size, user):
        connected(False, user)

    callbacks = [push, push, push, connected, disconnected, launched, launch_failed, mojo_error]
    initialize(0, None)
    environment = create()
    if not environment:
        raise RuntimeError("native_ocr_start_failed")
    # Retain the callback, wide path storage and loaded DLL until process exit.
    executable = ct.create_unicode_buffer(str(app / "Weixin.exe"))
    for index, function in enumerate(callbacks, 1):
        callback(environment, index, ct.cast(function, pointer))
    params(environment, 0, pointer(1))
    params(environment, 2, ct.cast(executable, pointer))
    for name, value in [
        (b"no-sandbox", ""),
        (b"user-lib-dir", str(runtime)),
        (b"app-path", str(plugin)),
        (b"type", "wxocr"),
    ]:
        switch(environment, name, value)
    start(environment)
    try:
        if not ready.wait(8) or errors:
            raise RuntimeError("native_ocr_start_failed")
        payload = field(1, 2) + field(2, str(image.resolve()).encode("utf-8"))
        payload += field(6, field(1, 1) + field(2, 1) + field(3, 0))
        message = create_write(1, False, 10010)
        if not message:
            raise RuntimeError("native_ocr_start_failed")
        buffer = write_buffer(message, len(payload))
        if not buffer:
            remove_write(message)
            raise RuntimeError("native_ocr_start_failed")
        ct.memmove(buffer, payload, len(payload))
        if not send(environment, message):
            remove_write(message)
            raise RuntimeError("native_ocr_start_failed")
        if not completed.wait(12):
            raise RuntimeError("native_ocr_timeout")
        if errors or not response or response[0].get(2, 0) != 0 or 3 not in response[0]:
            raise RuntimeError("native_ocr_failed")
        lines = []
        for number, value in fields(response[0][3]):
            if number == 3:
                line = dict(fields(value)).get(2, b"").decode("utf-8")
                if len(line) > 65536 or len(lines) >= 10000:
                    raise RuntimeError("native_ocr_invalid_result")
                lines.append(line)
        text = "\n".join(lines)
        if len(text) > 1048576:
            raise RuntimeError("native_ocr_invalid_result")
        return {"text": text, "lines": lines, "recognition": "wechat", "processing": "local"}
    finally:
        stop(environment)
        # Prevent callback/DLL GC before os._exit in the one-shot entry point.
        global _native_lifetime
        _native_lifetime = (lib, dll_directory, callbacks, executable)


if __name__ == "__main__":
    try:
        # Do not spawn WeChat until the supervising server has registered the
        # real interpreter PID (which may differ from its venv launcher PID).
        ready = Path(sys.argv[3])
        from wechat_cli.core.config import save_json

        save_json(ready, {"pid": os.getpid()})
        if sys.stdin.buffer.readline(16) != b"start\n":
            raise RuntimeError("native_ocr_start_failed")
        result = recognize(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as error:
        result = {"error": str(error) if str(error).startswith("native_") else "native_ocr_failed"}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    os._exit(0 if "error" not in result else 1)
