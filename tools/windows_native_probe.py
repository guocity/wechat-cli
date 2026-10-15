"""Read-only Windows Frida feasibility probe; this does not enable a chat backend.

Launch the extracted Weixin.exe in the desktop session first, then pass its PID.
No vendor functions are called and no account, chat or message data is read.
"""

import argparse
import hashlib
import json
import platform
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Windows":
        parser.error("Run this probe on Windows")
    import frida

    session = frida.get_local_device().attach(args.pid)
    try:
        script = session.create_script("""
            rpc.exports.inspect = function () {
                return {architecture: Process.arch, platform: Process.platform,
                    modules: Process.enumerateModules().filter(function(m) {
                        return /^(Weixin|WeChat)\\.(exe|dll)$/i.test(m.name);
                    }).map(function(m) { return {name:m.name,path:m.path,size:m.size}; })};
            };
        """)
        script.load()
        result = script.exports_sync.inspect()
        executable = next(m for m in result["modules"] if m["name"].lower().endswith(".exe"))
        if Path(executable["path"]).resolve() != args.exe.resolve():
            raise RuntimeError("PID belongs to a different executable")
        for module in result["modules"]:
            with Path(module.pop("path")).open("rb") as source:
                module["sha256"] = hashlib.file_digest(source, "sha256").hexdigest()
        result.update(attach=True, nativeChatBackend=False, vendorCalls=False)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
    finally:
        session.detach()


if __name__ == "__main__":
    main()
