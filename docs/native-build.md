# Building the macOS runtime assets

Users install and run the packaged CLI without Xcode or Command Line Tools.
The release includes arm64 binaries targeting macOS 13 or later:

- `native-reader.dylib`: C++ bridge copied into the prepared app and signed with it.
- `channels-native-v2.dylib`: C++ helper for the opt-in Channels research app.
- `native-controls`: Swift Accessibility controls executable.
- `silk.dylib`: standalone SILK SDK, loaded with ctypes in a bounded child process.

On an Apple Silicon build machine with Xcode Command Line Tools, run:

```sh
uv run python tools/build_macos_native.py
uv build
uv run python tools/check_wheel.py dist/wechat_cli-*.whl
```

The build script downloads the SHA-256-pinned pilk 0.2.4 source archive from PyPI,
compiles only the Skype-licensed SDK C sources and headers, and preserves the SDK
license in `assets/bin/SILK-LICENSE.txt`. It does not compile or distribute the
pilk Python extension or wrappers. For offline builds, pass
`--silk-archive /path/to/pilk-0.2.4.tar.gz`; the same checksum is mandatory.

All binaries are ad-hoc signed. The manifest records binary and first-party
source hashes. Packaging rejects stale or missing assets rather than compiling
on the installing computer. Rebuild after changing C++ or Swift sources.
Runtime verifies each helper's checksum and reports a reinstall error if the
asset is missing or damaged. The existing cross-platform wheel includes these
platform assets as data; Windows never loads them. The libraries do not depend
on a Python extension ABI.
