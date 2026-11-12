# Qualifying macOS WeChat releases

Check [Tencent's macOS release history](https://weixin.qq.com/updates?platform=mac) for the next release and download its official DMG. Keep the installer used for qualification outside the repo. The same public version and download URL can serve different builds. Record the public version, bundle version, build and full `wechat.dylib` SHA-256 from the installer.

## Inspect the release

On Apple Silicon, from the checkout:

```sh
uv sync --locked --group dev
uv run python tools/macos_release.py --dmg /path/to/WeChat.dmg
```

Use `--url "$installer_url"` to check a Tencent download directly or `--app /path/to/WeChat.app` to inspect a signed vendor app. The tool verifies Tencent's signature, checks for an arm64 slice and reports whether the payload already has a shipped profile. Add `--baseline /path/to/previous/WeChat.app` to produce relocation candidates using the previous app's shipped profile. The JSON includes matched functions, the length of each match, and unresolved functions. Keep this output outside the repo.

Create `src/wechat_cli/compatibility/macos-arm64/<version>-<build>.json` with the reported identity. Review every candidate you use against its call sites, argument and object layouts, and the expected execution context. Review data offsets, masked scan patterns, and exact-build guards in the macOS backend and agents. Set `"readOnly": true` while qualifying the core reads. Review individual capability guards for features that need separate validation.

```sh
profile='src/wechat_cli/compatibility/macos-arm64/<version>-<build>.json'
uv run python tools/macos_release.py --dmg /path/to/WeChat.dmg --profile "$profile"
```

Replace `<version>-<build>` with the actual profile name. The static check compares the identity, every function signature and each masked scan pattern in the profile against the vendor binary. Native behavior still requires the following live check.

## Qualify the candidate in isolation

Quit WeChat and stop the bridge service before switching copies. Choose a test chat with known messages and cached media. Mount the inspected DMG and point setup at the vendor app inside it.

```sh
state=$(mktemp -d)
uv run wechat-cli setup --data-dir "$state" --source /Volumes/WeChat/WeChat.app --pin-wechat
uv run wechat-cli start --data-dir "$state" --port 8790
```

Adjust the mounted app path as needed. Sign in through the test copy and run:

```sh
uv run python tools/live_smoke.py --data-dir "$state" --port 8790 \
  --qualify-profile "$profile" --require-media image --require-media voice --require-media file
```

The check requires the exact profile hash, a ready signed-in session, conversation and history results, string message IDs, and a successful text preparation dry run. Each requested media type must be readable from cache. Use `--chat-id` if your fixtures are in a chat other than File Transfer. Save the resulting counts and capability flags with the review. Check startup, login, paging through representative history and shutdown. Verify each additional capability you intend to advertise using an appropriate test account and fixtures.

## Ship the validated profile

Keep existing validated profiles for older managed copies. After live qualification, set `installer_url` to the official Tencent DMG URL and run:

```sh
uv run python tools/macos_release.py --url "$installer_url" --set-default
```

The command downloads the installer, verifies its signature, matches the payload hash to a profile in the checkout, and checks that profile's signatures. It then updates `src/wechat_cli/compatibility/macos.json` with the matching profile and installer URL. Record the build and the features verified in the release notes. Run the test suite and check the release wheel:

```sh
uv run pytest
uv run ruff check src tests tools
uv build --no-sources
uv run python tools/check_wheel.py /path/to/new.whl
```

Before publishing, check fresh setup and startup from a prior managed build using the new wheel. The wheel check compares the full profile inventory with the checkout and verifies the manifest references, so a new profile needs no packaging allowlist edit.

If a release causes a regression, stop the service, quit WeChat, and register the previous prepared app again:

```sh
uv run wechat-cli setup --app /path/to/previous/WeChatBridge.app --pin-wechat
uv run wechat-cli start
```

The previous path is recorded in `previous-native-runtime.json` in the service data directory after a managed upgrade. The pin applies to that data directory. To resume managed upgrades, quit WeChat and run `uv run wechat-cli upgrade`.
