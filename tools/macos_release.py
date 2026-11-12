"""Inspect, verify, or select the default Tencent-signed macOS WeChat release."""

import argparse
import json
import mmap
import plistlib
import re
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from map_profile import map_functions

from wechat_cli.backends.macos import setup
from wechat_cli.core.config import save_json
from wechat_cli.core.errors import AppError

PAYLOAD = Path("Contents/Resources/wechat.dylib")


def identity(app):
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    version = info["CFBundleShortVersionString"]
    bundle_version = info.get("WeChatBundleVersion", version)
    build = info["CFBundleVersion"]
    if not all(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", item) for item in (version, bundle_version, build)):
        raise ValueError("Expected numeric version and build in Info.plist")
    return {
        "version": version,
        "bundleVersion": bundle_version,
        "build": build,
        "arch": "arm64",
        "payloadSha256": setup.fingerprint(app / PAYLOAD),
    }


def extract_arm64(app, output):
    source = app / PAYLOAD
    architectures = setup.execute(["/usr/bin/lipo", "-archs", source]).split()
    if b"arm64" not in architectures:
        raise ValueError("The WeChat payload has no arm64 slice")
    if len(architectures) == 1:
        return source
    setup.execute(["/usr/bin/lipo", source, "-thin", "arm64", "-output", output])
    return output


def verify_profile(profile, release, arm64):
    if profile["version"] not in (release["version"], release["bundleVersion"]) or any(
        profile[field] != release[field] for field in ("build", "arch", "payloadSha256")
    ):
        raise ValueError("Profile identity does not match the app's version, build, architecture and SHA-256")

    signatures = 0
    for name, entry in profile.items():
        if not isinstance(entry, dict) or not ({"offset", "signature"} & entry.keys()):
            continue
        offset = int(entry["offset"], 16)
        signature = bytes.fromhex(entry["signature"])
        if offset < 0 or offset % 4 or len(signature) < 16 or len(signature) % 4:
            raise ValueError(f"{name}: invalid arm64 offset or signature")
        if arm64[offset : offset + len(signature)] != signature:
            raise ValueError(f"{name}: signature does not match the arm64 payload at {entry['offset']}")
        if "scanPattern" in entry:
            pattern, mask = (bytes.fromhex(part) for part in entry["scanPattern"].split(":"))
            data = arm64[offset : offset + len(pattern)]
            if (
                not pattern
                or len(data) != len(pattern)
                or len(mask) != len(pattern)
                or any((actual ^ expected) & bits for actual, expected, bits in zip(data, pattern, mask))
            ):
                raise ValueError(f"{name}: masked scan pattern does not match the arm64 payload")
        signatures += 1
    if not signatures:
        raise ValueError("Profile has no native signatures")
    return signatures


def inspect(app, directory, *, baseline=None, profile_path=None, verify_shipped=False):
    setup.verify_vendor_app(app)
    release = identity(app)
    path = extract_arm64(app, directory / "target.dylib")
    report = {"release": release}
    for shipped in sorted(setup.PROFILES.glob("*.json")):
        profile = json.loads(shipped.read_text())
        if profile["payloadSha256"] == release["payloadSha256"]:
            report["shippedProfile"] = shipped.name
            if verify_shipped:
                profile_path = shipped
            break
    if verify_shipped and profile_path is None:
        raise ValueError("The installer has no matching profile in the checkout")
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as arm64:
        if profile_path:
            profile = json.loads(profile_path.read_text())
            report["verifiedSignatures"] = verify_profile(profile, release, arm64)
        if baseline:
            profile = setup.check_payload(baseline)
            old_path = extract_arm64(baseline, directory / "baseline.dylib")
            with old_path.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as old:
                report["relocationCandidates"] = map_functions(profile, old, arm64)
    return report


def set_default(url, report):
    version = report["release"]["version"]
    manifest = {
        **setup.RELEASE,
        "profile": f"macos-arm64/{report['shippedProfile']}",
        "installerUrl": url,
        "releaseNotesUrl": "https://weixin.qq.com/updates?"
        + urlencode({"platform": "mac", "version": version}),
    }
    if manifest != setup.RELEASE:
        save_json(setup.PACKAGE / "compatibility/macos.json", manifest)
    report["default"] = manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--app", type=Path, help="Original signed WeChat.app")
    source.add_argument("--dmg", type=Path, help="Local Tencent DMG, mounted read-only")
    source.add_argument("--url", help="HTTPS installer URL to download and inspect")
    parser.add_argument("--baseline", type=Path, help="App with a shipped profile for relocation candidates")
    parser.add_argument("--profile", type=Path, help="Profile to check against the target payload")
    parser.add_argument(
        "--set-default",
        action="store_true",
        help="Select the URL's matching profile after live qualification",
    )
    args = parser.parse_args()
    if args.set_default:
        if not args.url or args.profile:
            parser.error("--set-default requires --url and selects the matching profile automatically")
        url = urlsplit(args.url)
        if url.scheme != "https" or not (url.hostname or "").endswith(".qq.com") or url.username:
            parser.error("--set-default requires an official Tencent HTTPS installer URL")
    try:
        setup.require_macos()
        with tempfile.TemporaryDirectory(prefix="wechat-release-") as temporary:
            directory = Path(temporary)
            options = {
                "baseline": args.baseline,
                "profile_path": args.profile,
                "verify_shipped": args.set_default,
            }
            if args.dmg or args.url:
                dmg = (
                    args.dmg.resolve()
                    if args.dmg
                    else setup.download(args.url, directory / "WeChat.dmg", 1024**3)
                )
                with setup.mounted_app(dmg, directory) as app:
                    report = inspect(app, directory, **options)
            else:
                report = inspect(args.app, directory, **options)
        if args.set_default:
            set_default(args.url, report)
    except (AppError, OSError, ValueError, TypeError, KeyError, plistlib.InvalidFileException) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
