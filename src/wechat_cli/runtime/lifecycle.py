"""One Uvicorn server owns one native session; commands reuse its local API."""

import json
import os
import socket
import subprocess
import sys
import time

import uvicorn

from wechat_cli.client import ApiClient
from wechat_cli.core.config import save_json
from wechat_cli.core.errors import AppError
from wechat_cli.host import default_data_directory, detached_options, lock_file


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
        from wechat_cli.backends.registry import platform_backend

        # Downloads/builds run here so their progress reaches the command line and
        # does not consume the background server's native-startup timeout.
        with lock_file(default_data_directory() / "instance.lock", blocking=False, instance=True):
            platform_backend().ensure_current(directory)
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
                **detached_options(),
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
    from wechat_cli.api.app import create_app

    with lock_file(directory / "server.lock", blocking=False):
        # Reserve the port before starting WeChat. No reload or multiple workers.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        app = None
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(128)
            actual_port = sock.getsockname()[1]

            def ready(application):
                save_json(
                    directory / "server.json",
                    {"port": actual_port, "instance": application.state.instance, "pid": os.getpid()},
                )
                print(
                    json.dumps(
                        {
                            "listening": f"http://127.0.0.1:{actual_port}",
                            "message": "Only one WeChat instance is allowed. Closing its main window or quitting WeChat shuts down this server.",
                        }
                    ),
                    flush=True,
                )

            app = create_app(directory, ready=ready)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=actual_port,
                    workers=1,
                    lifespan="on",
                    access_log=False,
                    log_config=None,
                    log_level="critical",
                    proxy_headers=False,
                    server_header=False,
                    timeout_graceful_shutdown=5,
                    limit_concurrency=32,
                )
            )
            app.state.shutdown = lambda: setattr(server, "should_exit", True)
            try:
                server.run(sockets=[sock])
            except SystemExit:
                # Uvicorn exits on lifespan startup failure; preserve our actionable CLI error.
                if app.state.startup_error:
                    raise app.state.startup_error from None
                raise
            if app.state.startup_error:
                raise app.state.startup_error
        except OSError as error:
            raise AppError(
                "server_bind_failed", f"Cannot bind 127.0.0.1:{port}; choose another --port", 409
            ) from error
        finally:
            sock.close()
            (directory / "server.json").unlink(missing_ok=True)
