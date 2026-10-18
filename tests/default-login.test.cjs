const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync(require('node:path').join(__dirname, '../src/wechat_cli/backends/macos/assets/default-login.js'), 'utf8');
const payload = '5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027';
function fixture({hash = payload, signatureValid = true, saved = true} = {}) {
  let interceptor, calls = 0;
  const bytes = new Map([
    [0x11be90c, 'fc6fbda9f44f01a9fd7b02a9fd830091ffc30bd1f30300aac84b0490084d46f9'],
    [0x11b7980, 'ff0306d1fc6f15a9f44f16a9fd7b17a9fdc30591f30300aae84b04f0084d46f9'],
  ]);
  class Pointer {
    constructor(value) { this.value = value; }
    add(offset) { return new Pointer(this.value + offset); }
    isNull() { return this.value === 0; }
    toString() { return String(this.value); }
    readByteArray() { return signatureValid ? Uint8Array.from(Buffer.from(bytes.get(this.value), 'hex')).buffer : new ArrayBuffer(32); }
    readPointer() { assert.equal(this.value, 0x20000008); return new Pointer(0x30000000); }
    readU8() { assert.ok([0x100000c0, 0x30000338].includes(this.value)); return saved ? 1 : 0; }
    readS8() { assert.equal(this.value, 0x3000028f); return 4; }
  }
  const context = {
    WECHAT_NATIVE_PROFILE: {payloadSha256: hash},
    Process: {arch: 'arm64', findRangeByAddress: () => ({protection: 'r-x'})},
    NativeFunction: function() { return window => { assert.equal(window.value, 0x30000000); calls++; }; },
    Interceptor: {attach(address, callbacks) { interceptor = callbacks; return {}; }},
  };
  vm.runInNewContext(source, context);
  const policy = context.WECHAT_DEFAULT_LOGIN;
  policy.install(new Pointer(0));
  return {policy, calls: () => calls, attached: () => !!interceptor, complete() {
    const invocation = {};
    interceptor.onEnter.call(invocation, [new Pointer(0x20000000), new Pointer(0x10000000)]);
    interceptor.onLeave.call(invocation);
  }};
}
test('unknown payload never installs or invokes native login', () => {
  const value = fixture({hash: 'unknown'});
  assert.equal(value.attached(), false);
  assert.equal(value.policy.status().supported, false);
});
test('changed instructions fail closed before attaching', () => {
  const value = fixture({signatureValid: false});
  assert.equal(value.attached(), false);
  assert.equal(value.policy.status().failure, 'native_login_signature_mismatch');
});
test('native callback without remembered credentials preserves manual login', () => {
  const value = fixture({saved: false});
  value.complete();
  assert.equal(value.calls(), 0);
  assert.equal(value.policy.status().savedAccountAvailable, false);
});
test('valid saved native account invokes login once and exposes no credential data', () => {
  const value = fixture();
  value.complete(); value.complete();
  assert.equal(value.calls(), 1);
  assert.equal(value.policy.status().loginStarted, true);
  assert.deepEqual(Object.keys(value.policy.status()).sort(), ['failure', 'installed', 'loginStarted', 'savedAccountAvailable', 'supported']);
});
