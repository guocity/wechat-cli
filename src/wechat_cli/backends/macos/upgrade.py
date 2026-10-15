"""Select the latest validated bridge before launch; never replace an installed app."""

import json
import sys
from pathlib import Path

from wechat_cli.core.config import save_json
from wechat_cli.core.errors import AppError
from wechat_cli.host import lock_file

from . import setup as installer
from .processes import check_instance

LATEST_PROFILE = installer.PACKAGE / "compatibility" / installer.RELEASE["profile"]


def release_order(profile):
    return tuple(int(part) for part in profile["version"].split(".")), int(profile["build"])


def ensure_current(directory):
    """Called under the session lock, including from foreground serve startup."""
    with lock_file(directory / "upgrade.lock"):
        runtime = installer.load_runtime(directory)
        current = installer.check_payload(Path(runtime["app"]))
        latest = json.loads(LATEST_PROFILE.read_text(encoding="utf-8"))
        if release_order(current) >= release_order(latest):
            return runtime
        executable = Path(runtime["app"]) / "Contents/MacOS/WeChat"
        if check_instance(executable) is not None:
            raise AppError(
                "wechat_update_requires_exit",
                f"Quit the running WeChat bridge app, then start again to upgrade it to {latest['version']}. Only one WeChat instance is allowed.",
                409,
            )
        destination = directory / "apps" / f"WeChatBridge-{latest['version']}-{latest['build']}.app"
        if destination.is_symlink():
            raise AppError(
                "invalid_destination", "The managed upgrade destination must not be a symlink", 409
            )
        # Earlier setup versions used an unversioned managed app directory.
        cached = directory / "apps/WeChatBridge.app"
        if not destination.exists() and cached.is_dir() and not cached.is_symlink():
            try:
                if installer.check_payload(cached)["payloadSha256"] == latest["payloadSha256"]:
                    destination = cached
            except AppError:
                pass  # Preserve an older/unrecognized copy and prepare separately.
        print(
            json.dumps(
                {
                    "event": "wechat_upgrade",
                    "from": current["version"],
                    "to": latest["version"],
                    "message": f"Preparing WeChat bridge {latest['version']} before startup. The existing app is preserved.",
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        # Prepare separately and verify the exact release before publishing the pointer.
        try:
            prepared = installer.setup(
                directory,
                destination=destination,
                publish=False,
                expected_payload=latest["payloadSha256"],
            )
            app = installer.check_app(prepared["app"])
            installer.expected_profile(app, latest["payloadSha256"])
            library = prepared["supportLibrary"]
            if (
                Path(library).name != library
                or not library.startswith("native-reader-")
                or not library.endswith(".dylib")
                or not (app / "Contents/Frameworks" / library).is_file()
            ):
                raise AppError("native_setup_required", "The prepared bridge helper is missing", 424)
            # Catch an app started manually during the download; don't create a second instance.
            if check_instance(executable) is not None:
                raise AppError(
                    "wechat_update_requires_exit",
                    "Quit WeChat and retry startup to finish the bridge upgrade",
                    409,
                )
            replacement = {"app": str(app), "supportLibrary": library}
            save_json(directory / "previous-native-runtime.json", runtime)
            save_json(directory / "native-runtime.json", replacement)
        except OSError as error:
            raise AppError(
                "wechat_upgrade_failed",
                "Could not prepare or activate the latest bridge copy. The previous bridge configuration was preserved; check disk space and write permissions, then retry.",
                424,
            ) from error
        print(
            json.dumps({"event": "wechat_upgraded", "version": latest["version"], "build": latest["build"]}),
            file=sys.stderr,
            flush=True,
        )
        return replacement
