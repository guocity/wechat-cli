// Private authenticated Gadget RPC; all code runs under the existing signing policy.
const framework=Process.enumerateModules().find(m=>m.name==='WeChatAppEx Framework');
function read64(address){const data=address.readVolatile(8);if(!data||data.byteLength!==8)throw Error('unreadable_native_metadata');return ptr(new DataView(data).getBigUint64(0,true).toString());}
function hex(address,n){return Array.from(new Uint8Array(address.readVolatile(n)),b=>b.toString(16).padStart(2,'0')).join('');}
const signatures={
  0xf74e4:'fc6fbaa9fa6701a9f85f02a9f65703a9',
  0x61b788:'fd7bbfa9fd030091e80300aa008005b0',
  0x3207ff8:'ff0301d1f44f02a9fd7b03a9fdc30091',
  0x5db6f68:'ff0302d1f85f04a9f65705a9f44f06a9'
};
function validate(){
  if(Process.arch!=='arm64'||Process.codeSigningPolicy!=='required'||!framework)throw Error('unsupported_native_channels_runtime');
  if(!framework.getExportByName('napi_run_script').sub(framework.base).equals(0x5db6f68))throw Error('native_channels_signature_mismatch');
  for(const [offset,expected] of Object.entries(signatures))if(hex(framework.base.add(Number(offset)),16)!==expected)throw Error('native_channels_signature_mismatch');
}
let helper,schedule,state,result;
const workers=new Map();
function validateWorker(isolate){
  const gin=read64(isolate.add(0x248));
  if(!read64(gin).equals(isolate))throw Error('native_worker_changed');
  const runner=read64(gin.add(168));
  const table=read64(runner);
  if(!table.equals(framework.base.add(0xb18ad10))||!read64(table.add(48)).equals(framework.base.add(0x3207ff8)))throw Error('native_task_runner_mismatch');
  const context=read64(isolate.add(63840));
  const cage=read64(framework.base.add(0xb7f3cb0));
  const map=cage.or(ptr(new DataView(context.sub(1).readVolatile(4)).getUint32(0,true)));
  if(new DataView(map.add(7).readVolatile(2)).getUint16(0,true)!==0xe4)throw Error('native_context_changed');
  return runner;
}
rpc.exports={
  identity(){return {pid:Process.id,path:Process.mainModule.path,policy:Process.codeSigningPolicy};},
  workers(){
    validate();workers.clear();
    const tls=framework.base.add(0xb61c228),key=tls.add(8).readU32(),offset=tls.add(12).readU32();
    if(key>=1024||offset>1048576)throw Error('unsupported_native_tls');
    const known=new Set(Process.enumerateThreads().filter(t=>t.name==='DedicatedWorker thread').map(t=>t.id));
    const task=Module.getGlobalExportByName('mach_task_self_').readU32();
    const array=Memory.alloc(8),count=Memory.alloc(4),info=Memory.alloc(48),infoCount=Memory.alloc(4);
    const taskThreads=new NativeFunction(Module.getGlobalExportByName('task_threads'),'int',['uint','pointer','pointer']);
    const threadInfo=new NativeFunction(Module.getGlobalExportByName('thread_info'),'int',['uint','uint','pointer','pointer']);
    const portRelease=new NativeFunction(Module.getGlobalExportByName('mach_port_deallocate'),'int',['uint','uint']);
    const memoryRelease=new NativeFunction(Module.getGlobalExportByName('vm_deallocate'),'int',['uint','pointer','uint64']);
    if(taskThreads(task,array,count)!==0)throw Error('native_threads_unavailable');
    const n=count.readU32(),ports=array.readPointer();
    try {
      for(let i=0;i<n;i++) {
        const id=ports.add(i*4).readU32();
        if(!known.has(id))continue;
        infoCount.writeU32(6);
        if(threadInfo(id,4,info,infoCount)!==0)continue;
        try {
          const block=read64(read64(info.add(8)).add(key*8));if(block.isNull())continue;
          const isolate=read64(block.add(offset));if(isolate.isNull())continue;
          validateWorker(isolate);workers.set(id,isolate);
        } catch(_){}
      }
    } finally {
      for(let i=0;i<n;i++)portRelease(task,ports.add(i*4).readU32());
      memoryRelease(task,ports,new UInt64(n*4));
    }
    return [...workers.keys()];
  },
  async evaluate(id,source){
    validate();
    const isolate=workers.get(id);
    if(!isolate||typeof source!=='string'||source.length>131072)throw Error('invalid_native_worker_request');
    const runner=validateWorker(isolate);
    if(!helper){
      helper=Module.load(CHANNELS_HELPER);
      schedule=new NativeFunction(helper.getExportByName('channels_evaluate_worker'),'int',['pointer','pointer','pointer']);
      state=new NativeFunction(helper.getExportByName('channels_state'),'int',[]);
      result=new NativeFunction(helper.getExportByName('channels_result'),'pointer',[]);
    }
    const buffer=Memory.allocUtf8String(source);
    if(schedule(runner,isolate,buffer)!==0)throw Error('native_media_busy');
    for(let i=0;i<60;i++) {
      const s=state();
      if(s!==2){if(s!==1)throw Error('native_worker_execution_failed');return result().readUtf8String();}
      await new Promise(resolve=>setTimeout(resolve,50));
    }
    throw Error('native_worker_timeout');
  }
};
