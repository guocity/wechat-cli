"""One persistent Python service per data directory; no PID-based killing."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from .client import ApiClient
from .config import private_directory, save_json
from .errors import AppError
from .native import NativeBridge


@contextmanager
def lock_file(path, blocking=True):
    private_directory(path.parent)
    with path.open("a+") as handle:
        path.chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise AppError(
                "server_already_running", "A service is already running for this data directory", 409
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def configured_port(directory, explicit=None):
    if explicit is not None:
        return explicit
    try:
        value = json.loads((directory / "server.json").read_text())
        return int(value["port"])
    except (OSError, ValueError, KeyError):
        return 8787


def health(directory, port):
    value = ApiClient(directory, port, timeout=1).request("/api/health")
    if value.get("service") != "wechat-cli":
        raise AppError("port_in_use", "Port belongs to another service", 409)
    return value


def ensure_server(directory, port, timeout=75):
    with lock_file(directory / "launch.lock"):
        try:
            return health(directory, port)
        except AppError as error:
            if error.code != "api_unavailable":
                raise
        # Wait for a shutting-down service to finish releasing its worker and socket.
        with lock_file(directory / "server.lock"):
            pass
        # Validate before spawning so configuration errors are immediately actionable.
        from .config import load_runtime

        load_runtime(directory)
        log = directory / "server.log"
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as output:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "wechat_cli",
                    "serve",
                    "--data-dir",
                    str(directory),
                    "--port",
                    str(port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                try:
                    error = json.loads(log.read_text().splitlines()[-1])
                    raise AppError(error["error"], error["message"], 424)
                except (ValueError, KeyError, IndexError):
                    raise AppError(
                        "server_start_failed", f"Service failed to start; inspect {log}", 424
                    ) from None
            try:
                return health(directory, port)
            except AppError as error:
                if error.code != "api_unavailable":
                    raise
            time.sleep(0.1)
        # This is our own child, never a process selected from a stale PID file.
        child.terminate()
        raise AppError("server_start_timeout", f"Service startup timed out; inspect {log}", 504)


def serve(directory, port):
    from .server import ApiServer

    with lock_file(directory / "server.lock", blocking=False):
        native = NativeBridge(directory)
        server = None
        try:
            # Connect eagerly: start really launches WeChat, rather than waiting for the first API call.
            native.connect()
            server = ApiServer(("127.0.0.1", port), directory, native=native)
            save_json(
                directory / "server.json",
                {"port": server.server_address[1], "instance": server.instance, "pid": os.getpid()},
            )

            def stop(_signal, _frame):
                threading.Thread(target=server.shutdown, daemon=True).start()

            previous = {sig: signal.signal(sig, stop) for sig in [signal.SIGINT, signal.SIGTERM]}
            print(json.dumps({"listening": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
            try:
                server.serve_forever(poll_interval=0.1)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        except OSError as error:
            raise AppError(
                "server_bind_failed", f"Cannot bind 127.0.0.1:{port}; choose another --port", 409
            ) from error
        finally:
            if server:
                server.server_close()
            else:
                native.close()
            (directory / "server.json").unlink(missing_ok=True)
