const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/wechat_cli/backends/windows/assets/transcription.js'), 'utf8');
function bridge() {
  const attached = new Map(), replaced = new Map(), reverted = [];
  let originalCalls = 0;
  const valid = { isNull: () => false, readPointer() { return this; } };
  const context = vm.createContext({
    profile: { transcription: { taskConstructor: 'constructor', taskFinished: 'finished', taskGuard: 'guard' } },
    entry: x => x, hooks: [], disposing: false, selfId: () => 'account',
    rpc: { exports: { dispose: async () => {} } },
    Process: { getCurrentThreadId: () => 7 },
    NativeFunction: function () { return () => { originalCalls++; return valid; }; },
    NativeCallback: function (fn) { return fn; },
    Interceptor: {
      attach(address, handlers) { attached.set(address, handlers); return { detach() {} }; },
      replace(address, callback) { replaced.set(address, callback); },
      revert(address) { reverted.push(address); },
    },
    setTimeout, clearTimeout, setImmediate,
  });
  vm.runInContext(source, context);
  return { context, attached, replaced, reverted, calls: () => originalCalls };
}
test('replacement preserves the guard and observes the nested constructor', async () => {
  const b = bridge();
  b.context.captureConstructor = job => {
    b.attached.get('constructor').onEnter.call({ threadId: 7 }, [{ toString: () => 'task' }]);
  };
  vm.runInContext(`
    const fixtureJob={status:'pending',text:'',created:Date.now(),retained:null};
    voiceTranscript=()=>{constructingVoiceJob={job:fixtureJob,thread:7};captureConstructor(fixtureJob);constructingVoiceJob=null;return voiceJobResult(fixtureJob);};
  `, b.context);
  const pending = b.context.rpc.exports.transcribeVoice({});
  assert.equal(b.replaced.size, 1);
  assert.equal(b.attached.has('guard'), false);
  b.replaced.get('guard')({});
  assert.equal((await pending).status, 'pending');
  assert.equal(b.calls(), 1);
  assert.equal(vm.runInContext('fixtureJob.task', b.context), 'task');
  await b.context.rpc.exports.dispose();
  assert.deepEqual(b.reverted, ['guard']);
});
test('reads and pending jobs do not retry; an explicit retry clears a finished failure', () => {
  const b = bridge();
  vm.runInContext(`
    const fixture={account:'account',chatId:'chat',id:'1'};
    const job={status:'failed',text:'',created:Date.now(),retained:null};
    voiceJobs.set('account|chat|1',job);
    nativeVoiceRecord=()=>{throw Error('retry-reached-native-validation');};
  `, b.context);
  assert.equal(vm.runInContext('voiceTranscript(fixture,false).status', b.context), 'failed');
  assert.throws(() => vm.runInContext('voiceTranscript(fixture,true)', b.context), /retry-reached-native-validation/);
  assert.equal(vm.runInContext('voiceJobs.size', b.context), 0);
  vm.runInContext(`job.status='pending';voiceJobs.set('account|chat|1',job);`, b.context);
  assert.equal(vm.runInContext('voiceTranscript(fixture,true).status', b.context), 'pending');
  vm.runInContext(`job.status='timed_out';job.task='active';voiceTasks.set('active',job);`, b.context);
  assert.equal(vm.runInContext('voiceTranscript(fixture,true).status', b.context), 'timed_out');
  assert.throws(() => vm.runInContext(`voiceTranscript({...fixture,account:'other'},true)`, b.context), /native_account_changed/);
});
test('disposing cancels queued work and restores the original native guard', async () => {
  const b = bridge();
  const pending = b.context.rpc.exports.transcribeVoice({});
  const rejected = assert.rejects(pending, /bridge_disconnected/);
  await b.context.rpc.exports.dispose();
  await rejected;
  assert.deepEqual(b.reverted, ['guard']);
});
