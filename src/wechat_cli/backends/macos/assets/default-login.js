// Native remembered-account login, enabled by default. No Accessibility/UI API.
// Current ABI recovered from WeChat 4.1.15 (270135), arm64. The saved-info
// continuation owns the live LoginWindow and calls us on its native thread.
(() => {
  const profile = globalThis.WECHAT_NATIVE_PROFILE;
  const state = {supported: false, installed: false, loginStarted: false,
    savedAccountAvailable: null, failure: null};
  const hooks = [];
  const attempted = new Set();
  let action = null;
  function snapshot() { return {...state}; }
  const policy = globalThis.WECHAT_DEFAULT_LOGIN = {
    status: snapshot,
    request: snapshot,
    install(base) {
      if (state.installed) return snapshot();
      if (Process.arch !== 'arm64' || profile?.payloadSha256 !==
          '5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027') return snapshot();
      try {
        const callback = base.add(0x11be90c);
        const entry = base.add(0x11b7980);
        for (const [address, signature] of [
          [callback, 'fc6fbda9f44f01a9fd7b02a9fd830091ffc30bd1f30300aac84b0490084d46f9'],
          [entry, 'ff0306d1fc6f15a9f44f16a9fd7b17a9fdc30591f30300aae84b04f0084d46f9'],
        ]) {
          const range = Process.findRangeByAddress(address);
          if (!range || !range.protection.includes('x')) throw Error('native_login_not_executable');
          const actual = Array.from(new Uint8Array(address.readByteArray(signature.length / 2)),
            value => value.toString(16).padStart(2, '0')).join('');
          if (actual !== signature) throw Error('native_login_signature_mismatch');
        }
        action = new NativeFunction(entry, 'void', ['pointer']);
        hooks.push(Interceptor.attach(callback, {
          onEnter(args) {
            this.window = null;
            try {
              // The continuation's captured window is a native, live pointer.
              // Only the valid optional record causes the native remembered
              // account action to run. Never print or return credential fields.
              if (args[1].add(0xc0).readU8() === 1) this.window = args[0].add(8).readPointer();
              else state.savedAccountAvailable = false;
            } catch (error) { state.failure = error.message; }
          },
          onLeave() {
            if (!this.window || this.window.isNull()) return;
            try {
              const window = this.window;
              if (window.add(0x338).readU8() !== 1) return;
              const marker = window.add(0x28f).readS8();
              const length = marker >= 0 ? marker : Number(window.add(0x280).readU64());
              if (length < 1 || length > 1024) { state.savedAccountAvailable = false; return; }
              state.savedAccountAvailable = true;
              const key = window.toString();
              if (attempted.has(key)) return;
              attempted.add(key);
              // Run after the native callback has copied and rendered the
              // remembered-account state, on that same native callback thread.
              action(window);
              state.loginStarted = true;
            } catch (error) { state.failure = error.message; }
          },
        }));
        state.supported = true;
        state.installed = true;
      } catch (error) { state.failure = error.message; }
      return snapshot();
    },
  };
})();
