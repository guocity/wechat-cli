"""Own one Frida session; communicate only over bounded JSON-line pipes."""

import json
import os
import sys
import threading
import time
from pathlib import Path

from .processes import launch
from .setup import check_app


def main():
    import frida

    from wechat_cli.core.config import save_json
    from wechat_cli.core.errors import AppError

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    output_lock = threading.Lock()
    stopped = threading.Event()

    def emit(value):
        with output_lock:
            print(json.dumps(value, ensure_ascii=True), flush=True)

    session = script = None
    try:
        info = check_app(os.environ["WECHAT_NATIVE_APP"])
        pid = launch(Path(info["executable"]))
        window_state = Path(os.environ["WECHAT_NATIVE_STATE"]) / "native-window.json"
        session = frida.get_local_device().attach(pid)

        def detached(reason, crash=None):
            stopped.set()
            emit(
                {
                    "event": "stopped",
                    "reason": "wechat_closed" if reason == "process-terminated" else "bridge_disconnected",
                }
            )

        session.on("detached", detached)
        source = "globalThis.WECHAT_WINDOWS_PROFILE=" + json.dumps(info) + ";\n"
        source += (Path(__file__).parent / "assets/agent.js").read_text(encoding="utf-8")
        source += "\n" + (Path(__file__).parent / "assets/transcription.js").read_text(encoding="utf-8")
        deadline = time.monotonic() + 30
        while True:
            try:
                script = session.create_script(source)
                startup_errors = []

                def message(value, data):
                    if value.get("type") == "error":
                        startup_errors.append(value.get("description", "native_agent_failed"))
                    elif (
                        value.get("type") == "send"
                        and value.get("payload", {}).get("event") == "window_changed"
                    ):
                        save_json(
                            window_state,
                            {
                                "pid": pid,
                                "window": value["payload"]["window"],
                                "payload": info["payloadSha256"],
                            },
                        )
                    elif value.get("type") == "send" and value.get("payload", {}).get("event") == "stopped":
                        stopped.set()
                        emit(value["payload"])

                script.on("message", message)
                script.load()
                if startup_errors:
                    raise RuntimeError(startup_errors[0])
                break
            except frida.InvalidOperationError:
                raise
            except frida.core.RPCException:
                raise
            except Exception:
                if script:
                    script.unload()
                    script = None
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        exports = script.exports_sync
        initial = exports.status()
        if not initial["gui"]["visible"]:
            try:
                previous = json.loads(window_state.read_text(encoding="utf-8"))
                if previous["pid"] == pid and previous["payload"] == info["payloadSha256"]:
                    exports.restore_gui(previous["window"])
            except (OSError, ValueError, KeyError, frida.core.RPCException):
                pass
        deadline = time.monotonic() + 30
        while not exports.status()["gui"]["visible"]:
            if stopped.is_set() or time.monotonic() >= deadline:
                raise AppError(
                    "native_gui_not_ready", "Open WeChat's main window, then start the service", 424
                )
            time.sleep(0.2)

        emit({"event": "ready", "protocol": 1, "pid": pid})
        for line in sys.stdin:
            if stopped.is_set():
                break
            request = json.loads(line)
            try:
                method, params = request["method"], request.get("params") or {}
                if method == "status":
                    result = exports.status()
                elif method == "databases":
                    result = exports.databases()
                elif method == "query":
                    result = exports.query(
                        params["database"],
                        params["sql"],
                        params.get("parameters", []),
                        params.get("maximum", 20000),
                    )
                elif method == "selfId":
                    result = exports.self_id()
                elif method in {"prepareVoice", "sendVoice"}:
                    from wechat_cli.core.media import decode_upload, silk_duration

                    data = decode_upload(params["dataBase64"], 1048576)
                    if silk_duration(data) != params["durationMs"]:
                        raise RuntimeError("invalid_voice")
                    result = getattr(exports, "prepare_voice" if method == "prepareVoice" else "send_voice")(
                        params["chatId"], list(data), params["durationMs"]
                    )
                elif method in {"voiceTranscript", "transcribeVoice"}:
                    result = getattr(
                        exports, "voice_transcript" if method == "voiceTranscript" else "transcribe_voice"
                    )(params)
                elif method == "mediaContext":
                    result = exports.media_context()
                elif method == "decodeImageHeader":
                    result = exports.decode_image_header(params["version"], params["bytes"])
                elif method in {"prepareFile", "sendFile"}:
                    result = getattr(exports, "prepare_file" if method == "prepareFile" else "send_file")(
                        params["chatId"], params["path"], params["name"], params["size"]
                    )
                elif method in {"prepareText", "sendText"}:
                    result = getattr(exports, "prepare_text" if method == "prepareText" else "send_text")(
                        params["chatId"], params["text"]
                    )
                else:
                    raise RuntimeError("native_feature_unavailable")
                emit({"id": request["id"], "result": result})
            except Exception as error:
                message = str(error).split("\n")[0].removeprefix("Error: ")
                code = (
                    message
                    if message.startswith(("native_", "invalid_", "unsupported_"))
                    else "native_read_failed"
                )
                emit({"id": request["id"], "error": code})
    except Exception as error:
        emit(
            {
                "event": "failure",
                "error": error.code if isinstance(error, AppError) else "native_bridge_unavailable",
            }
        )
        return 1
    finally:
        stopped.set()
        if script:
            try:
                script.exports_sync.dispose()
                script.unload()
            except Exception:
                pass
        if session:
            try:
                session.detach()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
