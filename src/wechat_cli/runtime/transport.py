"""Bounded subprocess transport. A blocked native call cannot hang the HTTP server."""

import json
import subprocess
import threading
from concurrent.futures import Future, TimeoutError

from wechat_cli.core.errors import AppError

SEND_METHODS = {"sendText", "sendVoice"}


class WorkerTransport:
    def __init__(self, command, environment, timeout=20, popen=subprocess.Popen):
        self.command, self.environment = command, environment
        self.timeout, self.popen = timeout, popen
        self.stopped = threading.Event()
        self.stop_reason = None
        self.pid = None
        self.started = False
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.process = None
        self.ready = None
        self.closed = False
        self.observed = {"connected": False}

    def connect(self):
        with self.connect_lock:
            if self.closed or self.stopped.is_set():
                raise AppError("native_bridge_unavailable", "Native bridge is closed", 503)
            if self.process and self.process.poll() is None:
                return
            if self.started:
                raise AppError(
                    "native_bridge_unavailable", "WeChat or its bridge closed; start the service again", 503
                )
            self.started = True
            self.ready = Future()
            try:
                process = self.popen(
                    self.command,
                    env=self.environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError:
                raise AppError(
                    "native_bridge_unavailable", "Cannot launch the Python Frida worker", 424
                ) from None
            self.process = process
            threading.Thread(target=self._read, args=(process, self.ready), daemon=True).start()
            try:
                self.ready.result(timeout=max(60, self.timeout))
            except TimeoutError:
                process.terminate()
                raise AppError(
                    "native_bridge_timeout", "WeChat did not start in time; inspect its window and retry", 504
                ) from None

    def _read(self, process, ready):
        try:
            for line in process.stdout:
                value = json.loads(line)
                if value.get("event") == "ready":
                    if value.get("protocol") != 1:
                        raise ValueError("unsupported_worker_protocol")
                    self.pid = value.get("pid")
                    if not ready.done():
                        ready.set_result(True)
                elif value.get("event") == "stopped":
                    self.stop_reason = value.get("reason", "bridge_disconnected")
                    self.stopped.set()
                elif value.get("event") == "failure":
                    if not ready.done():
                        code = value.get("error", "native_bridge_unavailable")
                        ready.set_exception(
                            AppError(
                                code,
                                (
                                    "Only one WeChat instance is allowed. Quit the other WeChat app or stop its wechat-cli server, then retry."
                                    if code == "wechat_instance_conflict"
                                    else "Cannot connect to the prepared WeChat app. Run doctor and retry."
                                ),
                                424,
                            )
                        )
                elif "id" in value:
                    with self.lock:
                        entry = self.pending.pop(value["id"], None)
                    if entry:
                        method, future = entry
                        if "error" in value:
                            code = value["error"]
                            if method in SEND_METHODS and code in {
                                "native_read_failed",
                                "native_send_failed",
                            }:
                                code = "native_send_outcome_unknown"
                            messages = {
                                "native_account_not_ready": "Sign into the configured WeChat app, then retry.",
                                "native_task_context_unavailable": "WeChat is starting or busy. Open its main window and retry.",
                                "native_send_outcome_unknown": "Send outcome is unknown. Inspect WeChat history and reuse the same key for retries.",
                                "native_feature_unavailable": "This feature has not been validated for this WeChat payload.",
                            }
                            future.set_exception(
                                AppError(code, messages.get(code, code.replace("_", " ")), 409)
                            )
                        else:
                            future.set_result(value.get("result"))
        except (OSError, ValueError):
            pass
        finally:
            if not ready.done():
                ready.set_exception(
                    AppError("native_bridge_unavailable", "Frida worker exited during startup", 424)
                )
            with self.lock:
                # Reader of an old process must never fail a replacement process's requests.
                if self.process is process:
                    self.observed["connected"] = False
                    self.stop_reason = self.stop_reason or "bridge_disconnected"
                    self.stopped.set()
                    for method, future in self.pending.values():
                        code = (
                            "native_send_outcome_unknown"
                            if method in SEND_METHODS
                            else "native_bridge_unavailable"
                        )
                        future.set_exception(
                            AppError(
                                code, "Worker disconnected. Check WeChat history before retrying a send.", 502
                            )
                        )
                    self.pending.clear()
            process.stdout.close()

    def request(self, method, params=None):
        self.connect()
        future = Future()
        with self.lock:
            if len(self.pending) >= 8:
                raise AppError("native_bridge_busy", "Too many pending native requests", 429)
            self.sequence += 1
            identifier = self.sequence
            self.pending[identifier] = method, future
            try:
                self.process.stdin.write(
                    json.dumps({"id": identifier, "method": method, "params": params or {}}) + "\n"
                )
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.pending.pop(identifier, None)
                code = (
                    "native_send_outcome_unknown" if method in SEND_METHODS else "native_bridge_unavailable"
                )
                raise AppError(code, "Worker disconnected; check WeChat before retrying", 502) from None
        try:
            return future.result(timeout=65 if method == "extractImageText" else self.timeout)
        except TimeoutError:
            with self.lock:
                self.pending.pop(identifier, None)
            code = "native_send_outcome_unknown" if method in SEND_METHODS else "native_read_timeout"
            raise AppError(code, "Native call timed out; check WeChat before retrying", 504) from None

    def close(self):
        self.closed = True
        self.stopped.set()
        process = self.process
        if process:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        # Closing the service detaches; it never logs the user out.
