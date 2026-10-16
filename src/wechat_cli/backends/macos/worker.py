"""Authenticated local Gadget RPC transport. No UI automation; native chat reads and text sending."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import frida

from wechat_cli.backends.macos.processes import check_instance
from wechat_cli.backends.macos.setup import check_payload

package = pathlib.Path(__file__).resolve().parent / "assets"
app = pathlib.Path(os.environ["WECHAT_NATIVE_APP"]).resolve()
data_dir = pathlib.Path(os.environ["WECHAT_DATA_DIR"])
config = json.loads((app / "Contents/Frameworks/FridaGadget.config").read_text(encoding="utf-8"))

profile = check_payload(app)
source = (package / "startup-notice.js").read_text(encoding="utf-8") + "\n"
source += (package / "gui-lifecycle.js").read_text(encoding="utf-8") + "\n"
source += "globalThis.WECHAT_NATIVE_PROFILE=" + json.dumps(profile) + ";\n"
support = os.environ["WECHAT_NATIVE_SUPPORT"]
if (
    not support.startswith("native-reader-")
    or not support.endswith(".dylib")
    or pathlib.Path(support).name != support
):
    raise RuntimeError("invalid_native_runtime")
source += "globalThis.WECHAT_NATIVE_SUPPORT=" + json.dumps(str(app / "Contents/Frameworks" / support)) + ";\n"
source += (package / "native-agent.js").read_text(encoding="utf-8")
endpoint = "127.0.0.1:" + str(config["interaction"]["port"])
session = device = script = child = None
output_lock = threading.Lock()


def emit(value):
    with output_lock:
        print(json.dumps(value), flush=True)


def detached(reason, crash=None):
    emit(
        {
            "event": "stopped",
            "reason": "wechat_closed" if reason == "process-terminated" else "bridge_disconnected",
        }
    )


def script_message(message, data):
    if message.get("type") == "error":
        emit({"event": "stopped", "reason": "bridge_script_failed"})
    if message.get("type") == "send" and message.get("payload") == {"event": "gui_closed"}:
        emit({"event": "stopped", "reason": "gui_closed"})


try:
    check_instance(app / "Contents/MacOS/WeChat")
    digest = hashlib.sha256()
    with (app / "Contents/Resources/wechat.dylib").open("rb") as binary:
        for block in iter(lambda: binary.read(1048576), b""):
            digest.update(block)
    if digest.hexdigest() != profile["payloadSha256"]:
        raise RuntimeError("native_payload_mismatch")
    device = frida.get_device_manager().add_remote_device(endpoint, token=config["interaction"]["token"])
    try:
        session = device.attach("Gadget")
    except frida.ServerNotRunningError:
        # A starting app can exist before Gadget begins listening. Launching
        # another copy in that interval creates our own single-instance conflict.
        if check_instance(app / "Contents/MacOS/WeChat") is None:
            child = subprocess.Popen(
                ["/usr/bin/open", "-n", "-a", str(app)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        for attempt in range(250):
            if child is not None and child.poll() not in {None, 0}:
                raise RuntimeError("native_copy_exited")
            try:
                session = device.attach("Gadget")
                break
            except (frida.TransportError, frida.ServerNotRunningError, frida.TimedOutError):
                time.sleep(0.2)
        else:
            raise RuntimeError("native_bridge_timeout")
    session.on("detached", detached)
    script = session.create_script(source)
    script.on("message", script_message)
    script.load()
    identity = script.exports_sync.startup_status()
    if pathlib.Path(identity["executable"]).resolve() != (app / "Contents/MacOS/WeChat").resolve():
        raise RuntimeError("native_process_identity_mismatch")
    if not check_instance(app / "Contents/MacOS/WeChat", identity["pid"]):
        raise RuntimeError("wechat_closed")
    # Resume a Gadget-waiting app even after an earlier gateway startup timed out.
    if child is not None or not identity.get("applicationRunning"):
        try:
            device.resume(identity["pid"])
        except frida.InvalidOperationError:
            pass
    emit({"event": "ready", "protocol": 1, "pid": identity["pid"]})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if request["method"] == "status":
                result = script.exports_sync.status()
            elif request["method"] == "probeTextProcessor":
                result = script.exports_sync.probe_text_processor()
            elif request["method"] == "prepareText":
                result = script.exports_sync.prepare_text(
                    request["params"]["chatId"], request["params"]["text"]
                )
            elif request["method"] == "sendText":
                result = script.exports_sync.send_text(request["params"]["chatId"], request["params"]["text"])
            elif request["method"] == "selfId":
                result = script.exports_sync.self_id()
            elif request["method"] == "importControl":
                result = script.exports_sync.import_control(request["params"]["action"])
            elif request["method"] == "migrationStatus":
                result = script.exports_sync.migration_status()
            elif request["method"] == "microphoneStatus":
                result = script.exports_sync.microphone_status()
            elif request["method"] == "armRecording":
                result = script.exports_sync.arm_recording(request["params"]["chatId"])
            elif request["method"] == "recordingResult":
                result = script.exports_sync.recording_result()
            elif request["method"] == "discardRecording":
                result = script.exports_sync.discard_recording()
            elif request["method"] in ["sendVoice", "prepareVoice"]:
                result = script.exports_sync.send_voice(
                    request["params"]["chatId"],
                    request["params"]["dataBase64"],
                    request["params"]["durationMs"],
                    request["method"] == "prepareVoice",
                )
            elif request["method"] == "attachmentData":
                result = script.exports_sync.attachment_data(
                    request["params"]["chatId"], request["params"]["id"]
                )
            elif request["method"] == "voiceData":
                result = script.exports_sync.voice_data(request["params"]["chatId"], request["params"]["id"])
            elif request["method"] == "transcribeVoice":
                result = script.exports_sync.transcribe_voice(
                    request["params"]["chatId"], request["params"]["id"]
                )
            elif request["method"] == "extractImageText":
                result = script.exports_sync.extract_image_text(request["params"]["dataBase64"])
            elif request["method"] == "voiceTranscriptionResult":
                result = script.exports_sync.voice_transcription_result(
                    request["params"]["chatId"], request["params"]["id"]
                )
            elif request["method"] == "cachedMedia":
                result = script.exports_sync.cached_media(request["params"]["hash"])
            elif request["method"] == "listChats":
                result = script.exports_sync.list_chats()
            elif request["method"] in ["listMessages", "rawMessages"]:
                result = script.exports_sync.list_messages(
                    request["params"]["chatId"], request["params"]["limit"]
                )
            else:
                raise RuntimeError("native_method_unimplemented")
            emit({"id": request["id"], "result": result})
        except Exception as error:
            # Private diagnostic for native call failures. Never sent to callers
            # and never logged with chat/secret values.
            if isinstance(error, frida.core.RPCException):
                description = str(error).split("\n")[0]
                if request["method"] == "prepareVoice" or description.startswith(
                    (
                        "Error: access violation",
                        "Error: invalid address",
                        "TypeError:",
                        "Error: native_",
                        "Error: recursive_mutex",
                    )
                ):
                    diagnostic = data_dir / "native-rpc-error.json"
                    diagnostic.write_text(
                        json.dumps({"method": request["method"], "description": description}) + "\n"
                    )
                    diagnostic.chmod(0o600)
            code = "native_read_failed"
            if isinstance(error, frida.core.RPCException):
                for known in [
                    "native_feature_unavailable",
                    "native_invalid_media_id",
                    "native_migration_busy",
                    "native_migration_failed",
                    "native_recording_unavailable",
                    "native_recording_invalid",
                    "native_recording_recipient_changed",
                    "native_send_outcome_unknown",
                    "native_voice_layout_unverified",
                    "invalid_voice",
                    "native_voice_prepare_failed",
                    "native_ocr_not_ready",
                    "native_ocr_failed",
                    "native_invalid_ocr_image",
                    "native_ocr_layout_unverified",
                    "native_message_not_found",
                    "invalid_media_type",
                    "native_transcription_failed",
                    "native_conversations_not_observed",
                    "native_account_not_ready",
                    "native_signature_mismatch",
                    "native_module_missing",
                    "native_chat_not_found",
                    "native_message_layout_unverified",
                    "native_services_not_ready",
                    "native_message_service_not_ready",
                    "native_task_context_unavailable",
                    "native_send_failed",
                    "native_invalid_recipient",
                    "invalid_chat_id",
                    "invalid_text",
                    "native_bridge_busy",
                    "native_session_service_not_ready",
                    "native_session_layout_unverified",
                ]:
                    if known in str(error):
                        code = known
                        break
            emit({"id": request["id"], "error": code})
except Exception as error:
    code = getattr(
        error, "code", str(error) if isinstance(error, RuntimeError) else "native_bridge_unavailable"
    )
    emit({"event": "failure", "error": code})
finally:
    if script is not None:
        try:
            script.exports_sync.dispose_gui()
        except Exception:
            pass
        try:
            script.unload()
        except Exception:
            pass
    if session is not None:
        try:
            session.detach()
        except Exception:
            pass
    if device is not None:
        frida.get_device_manager().remove_remote_device(endpoint)
    # Keep the native app running for its normal login/session lifecycle.
    # This transport never logs out or terminates the user's app on API close.
