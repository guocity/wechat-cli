const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const crypto=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/wechat_cli/backends/macos/assets/channels-confirm-login.js'),'utf8');
const signatures=new Map([
[0xf4a8a0,'ff8301d1f65703a9f44f04a9fd7b05a9fd430191f40302aaf50301aaf30300aa'],
[0xf4ab48,'f44fbea9fd7b01a9fd430091f30300aae86204b00861399109410091090000f9'],
[0xf4abdc,'fc6fbca9f65701a9f44f02a9fd7b03a9fdc30091ff030ad1f40301aaf30300aa'],
[0xf4bcd0,'1f040071e0000054a0000035810000b4e00301aa01048052c9d08715c0035fd6']]);
function fixture({app='视频号创作平台',scope='snsapi_login',nonce='native-nonce',valid=true}={}){
 const hooks=new Map(),timers=new Map(),memory=new Map();let alloc=0x20000000,calls=0,timerId=0,now=1000;
 const w=0x10000000,fields=new Map([[w+0x228,app],[w+0x260,scope],[w+0x298,nonce]]);
 class P {
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}toString(){return String(this.n);}equals(p){return this.n===p.n;}
 readByteArray(){return memory.get(this.n)||Uint8Array.from(Buffer.from(valid?signatures.get(this.n):'00'.repeat(32),'hex')).buffer;}
 readPointer(){return this.n===w?new P(0x9ba7e68):memory.get(this.n);}
 readU8(){return memory.get(this.n)||0;}writeU8(v){memory.set(this.n,v);}writePointer(v){memory.set(this.n,v);}
 readS8(){return fields.get(this.n-23).length;}readUtf8String(){return fields.get(this.n)||memory.get(this.n);}
 }
 const context={Date:{now:()=>now},WECHAT_NATIVE_PROFILE:{payloadSha256:'5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027'},
 Process:{arch:'arm64',findRangeByAddress:()=>({protection:'r-x'})},
 Memory:{alloc(){return new P(alloc+=256);},allocUtf8String(v){const p=new P(alloc+=256);memory.set(p.n,v);return p;}},
 Module:{getGlobalExportByName:n=>n},ptr:n=>new P(n),
 NativeCallback:function(fn){return fn;},NativeFunction:function(address){
 if(address==='CC_SHA256')return (input,size,out)=>memory.set(out.n,Uint8Array.from(crypto.createHash('sha256').update(input.readUtf8String()).digest()).buffer);
 if(address==='dispatch_sync_f')return (queue,p,fn)=>fn(p);
 return (op,capture)=>{assert.equal(op,1);assert.equal(capture.add(0x10).readU8(),1);assert.equal(capture.add(0x18).readPointer().n,w);calls++;};
 },Interceptor:{attach(p,h){hooks.set(p.n,h);return {};}},
 setTimeout(fn){timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);}};
 vm.runInNewContext(source,context);const policy=context.WECHAT_CHANNELS_LOGIN;policy.install(new P(0));
 return {policy,calls:()=>calls,advance(ms){now+=ms;},arm(){return policy.arm('12345678-1234-1234-1234-123456789abc',crypto.createHash('sha256').update('native-nonce').digest('hex'));},
 create(){const h=hooks.get(0xf4a8a0),inv={};h.onEnter.call(inv,[new P(w)]);h.onLeave.call(inv);},destroy(){hooks.get(0xf4ab48).onEnter([new P(w)]);},flush(){for(const fn of [...timers.values()])fn();timers.clear();}};
}
test('changed native instructions prevent consent hook installation',()=>{const f=fixture({valid:false});assert.equal(f.policy.status().supported,false);});
test('unarmed native confirmation is never accepted',()=>{const f=fixture();f.create();f.flush();assert.equal(f.calls(),0);});
for(const [name,options] of [['foreign app',{app:'Other app'}],['foreign scope',{scope:'other_scope'}]])
 test(name+' is never accepted',()=>{const f=fixture(options);f.arm();f.create();f.flush();assert.equal(f.calls(),0);});
test('correlated native confirmation invokes the actual affirmative callback once',()=>{const f=fixture();f.arm();f.create();assert.equal(f.calls(),0);f.flush();f.create();f.flush();assert.equal(f.calls(),1);assert.equal(f.policy.status().armed,false);});
test('destroyed confirmation cannot receive a deferred callback',()=>{const f=fixture();f.arm();f.create();f.destroy();f.flush();assert.equal(f.calls(),0);});
test('disposal cancels native confirmation work',()=>{const f=fixture();f.arm();f.create();f.policy.dispose();f.flush();assert.equal(f.calls(),0);});

test('expired operation cannot confirm a newly created window',()=>{const f=fixture();f.arm();f.advance(30001);f.create();f.flush();assert.equal(f.calls(),0);});
test('arming never confirms a window that was already open',()=>{const f=fixture();f.create();f.arm();f.flush();assert.equal(f.calls(),0);});
