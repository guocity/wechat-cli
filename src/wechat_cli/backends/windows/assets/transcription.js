// Exact-build native recognition. WeChat owns all asynchronous callbacks;
// the bridge only observes completion and holds native account services alive.
const voiceJobs=new Map(),voiceTasks=new Map();
let constructingVoiceJob=null;
const voiceProfile=profile.transcription;
function voiceFunction(name,result,args){return new NativeFunction(entry(voiceProfile[name]),result,args,{exceptions:'steal',traps:'all'});}
function sharedSlot(){const p=Memory.alloc(16);p.writeByteArray(new Uint8Array(16));return p;}
function clearShared(p){p.writePointer(ptr(0));p.add(8).writePointer(ptr(0));}
function nativeVoiceRecord(record){
  if(!record||record.account!==selfId()||typeof record.chatId!=='string'||!/^\d{1,10}$/.test(record.id)||Number(record.id)>0xffffffff||typeof record.content!=='string'||record.content.length>1048576||typeof record.source!=='string'||record.source.length>1048576||!Array.isArray(record.packed)||record.packed.length>1048576||record.packed.some(b=>!Number.isInteger(b)||b<0||b>255)||!/^\d{1,20}$/.test(record.serverId)||!/^\d{1,20}$/.test(record.sortSequence)||!Number.isInteger(record.timestamp)||record.timestamp<0||record.timestamp>0xffffffff)throw Error('invalid_input');
  const source=Memory.alloc(0x2e8),owned=sharedSlot();source.writeByteArray(new Uint8Array(0x2e8));
  voiceFunction('messageConstructor','pointer',['pointer'])(source);
  try{
    const from=record.outgoing?record.account:record.chatId,to=record.outgoing?record.chatId:record.account;
    for(const [offset,text] of [[0x18,from],[0x38,to],[0x58,record.sender||from],[0x180,record.content],[0x1c0,record.source]])assignEmptyString(source.add(offset),text);
    source.add(0xc).writeU32(34);source.add(0x144).writeU32(Number(record.id));source.add(0x148).writeU64(uint64(record.serverId));source.add(0x150).writeU64(uint64(record.sortSequence));source.add(0x158).writeU32(34);source.add(0x164).writeU32(record.timestamp);source.add(0x170).writeU64(uint64(record.id));
    if(record.packed.length){const data=Memory.alloc(record.packed.length);data.writeByteArray(record.packed);if(!voiceFunction('parsePacked','uchar',['pointer','pointer','int'])(source.add(0x1f8),data,record.packed.length))throw Error('native_message_decode_failed');}
    voiceFunction('parseMessage','void',['pointer','uchar'])(source,1);
    const extension=source.add(0x288).readPointer();
    if(extension.isNull()||!extension.readPointer().equals(module.base.add(voiceProfile.voiceVtable))||extension.add(0x224).readU32()<1||extension.add(0x224).readU32()>60000)throw Error('native_voice_layout_unverified');
    const sourceReference=Memory.alloc(8);sourceReference.writePointer(source);
    voiceFunction('copyMessage','pointer',['pointer','pointer'])(owned,sourceReference);
    return owned;
  }finally{voiceFunction('messageDestructor','pointer',['pointer','uint'])(source,0);}
}
function voiceJobResult(job){
  if(job.status==='pending'&&Date.now()-job.created>90000)job.status='timed_out';
  return {status:job.status,text:job.status==='completed'?job.text:null};
}
function releaseVoiceContext(job){
  if(job.retained){for(const p of job.retained.reverse())releaseShared(p);job.retained=null;}
}
hooks.push(Interceptor.attach(entry(voiceProfile.taskConstructor),{
  onEnter(args){if(constructingVoiceJob&&constructingVoiceJob.thread===this.threadId){const job=constructingVoiceJob.job;job.task=args[0].toString();voiceTasks.set(args[0].toString(),job);}}
}));
hooks.push(Interceptor.attach(entry(voiceProfile.taskFinished),{
  onEnter(args){
    const job=voiceTasks.get(args[0].toString());if(!job)return;
    this.job=job;
    try{const text=readString(args[2],1048576);this.result={status:args[1].toInt32()===7&&text.trim()?'completed':'failed',text};}
    catch(_){this.result={status:'failed',text:''};}
  },
  onLeave(){if(!this.job)return;const job=this.job;if(job.status==='pending'){job.status=this.result.status;job.text=this.result.text;}voiceTasks.delete(job.task);setImmediate(()=>releaseVoiceContext(job));}
}));
function voiceTranscript(record,trigger){
  const account=selfId();if(!record||record.account!==account)throw Error('native_account_changed');
  const key=account+'|'+record.chatId+'|'+record.id;
  for(const [k,j] of voiceJobs){if(j.status!=='pending'&&Date.now()-j.created>300000){releaseVoiceContext(j);voiceJobs.delete(k);}}
  const previous=voiceJobs.get(key);
  if(previous){
    const value=voiceJobResult(previous);
    if(!trigger||!['failed','timed_out'].includes(value.status)||voiceTasks.has(previous.task))return value;
    releaseVoiceContext(previous);voiceJobs.delete(key);
  }
  if(!trigger)return {status:'unavailable',text:null};
  const owned=nativeVoiceRecord(record);
  try{
    if(voiceJobs.size>=32||[...voiceJobs.values()].filter(j=>voiceJobResult(j).status==='pending').length>=2)throw Error('native_bridge_busy');
    const job={account,created:Date.now(),status:'pending',text:'',retained:[]};
    const accountRef=sharedSlot(),services=sharedSlot(),messages=sharedSlot(),front=sharedSlot();job.retained=[accountRef,services,messages,front];
    const type=Memory.alloc(4),subtype=Memory.alloc(4);type.writeU32(34);subtype.writeU32(0);
    try{
      voiceFunction('account','pointer',['pointer'])(accountRef);
      if(accountRef.readPointer().isNull())throw Error('native_account_not_ready');
      voiceFunction('services','pointer',['pointer','pointer'])(accountRef.readPointer(),services);
      if(services.readPointer().isNull())throw Error('native_services_not_ready');
      voiceFunction('messages','pointer',['pointer','pointer'])(services.readPointer(),messages);
      if(messages.readPointer().isNull())throw Error('native_services_not_ready');
      voiceFunction('front','pointer',['pointer','pointer','pointer','pointer'])(messages.readPointer(),front,type,subtype);
      if(front.readPointer().isNull()||selfId()!==account)throw Error('native_services_not_ready');
      const result=Memory.alloc(32),success=Memory.alloc(64),failure=Memory.alloc(64);
      result.writeByteArray(new Uint8Array(32));result.add(24).writeU64(15);success.writeByteArray(new Uint8Array(64));failure.writeByteArray(new Uint8Array(64));
      voiceJobs.set(key,job);constructingVoiceJob={job,thread:Process.getCurrentThreadId()};
      try{
        // The vendor factory takes the shared message parameter by value.
        voiceFunction('start','pointer',['pointer','pointer','pointer','pointer','pointer'])(front.readPointer(),result,owned,success,failure);
        clearShared(owned);
      }finally{constructingVoiceJob=null;voiceFunction('stringDestroy','void',['pointer'])(result);}
      if(!job.task)throw Error('native_transcription_failed');
      return voiceJobResult(job);
    }catch(error){job.status='failed';if(!job.task)releaseVoiceContext(job);throw error;}
  }finally{releaseShared(owned);}
}
// The service registry requires an active native scheduler context. Borrow
// the next vendor task after its guard is established; never invent TLS state.
const voiceQueue=new Map();let voiceQueueSequence=0,insideVoiceTask=false,activeVoiceGuards=0;
const voiceGuardAddress=entry(voiceProfile.taskGuard);
const originalVoiceGuard=new NativeFunction(voiceGuardAddress,'pointer',['pointer']);
// An attach callback suppresses nested constructor hooks on its own thread.
// A replacement calls the original guard and permits observing our task below.
const replacementVoiceGuard=new NativeCallback(function(output){
  activeVoiceGuards++;
  try{
    const result=originalVoiceGuard(output);
    if(insideVoiceTask||!voiceQueue.size||disposing||result.isNull()||result.readPointer().isNull())return result;
    const [id,item]=voiceQueue.entries().next().value;
    voiceQueue.delete(id);clearTimeout(item.timer);insideVoiceTask=true;
    try{const value=item.action();setImmediate(()=>item.resolve(value));}
    catch(error){setImmediate(()=>item.reject(error));}
    finally{insideVoiceTask=false;}
    return result;
  }finally{activeVoiceGuards--;}
},'pointer',['pointer']);
Interceptor.replace(voiceGuardAddress,replacementVoiceGuard);
function voiceInNativeTask(action){
  if(voiceQueue.size>=2)throw Error('native_bridge_busy');
  return new Promise((resolve,reject)=>{
    const id=++voiceQueueSequence;
    const timer=setTimeout(()=>{voiceQueue.delete(id);reject(Error('native_services_not_ready'));},5000);
    voiceQueue.set(id,{action,resolve,reject,timer});
  });
}
rpc.exports.voiceTranscript=record=>voiceTranscript(record,false);
rpc.exports.transcribeVoice=record=>voiceInNativeTask(()=>voiceTranscript(record,true));
const disposeVoiceAgent=rpc.exports.dispose;
rpc.exports.dispose=async()=>{for(const item of voiceQueue.values()){clearTimeout(item.timer);item.reject(Error('bridge_disconnected'));}voiceQueue.clear();while(activeVoiceGuards)await new Promise(resolve=>setTimeout(resolve,10));Interceptor.revert(voiceGuardAddress);await disposeVoiceAgent();for(const job of voiceJobs.values())releaseVoiceContext(job);voiceTasks.clear();voiceJobs.clear();};
