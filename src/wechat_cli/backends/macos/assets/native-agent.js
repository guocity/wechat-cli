// Frida runtime script, not a Node module. Loaded only by the native bridge.
// Read native results; message calls run inside a registered native task context.
const nativeProfile = globalThis.WECHAT_NATIVE_PROFILE;
const nativeState = { state: 'waiting_for_module', protocol: 'wechat-macos',
  connected: false, installed: false, enumerationObserved: false, chats: [],
  messageReadsObserved: 0, failure: null };
let liveNativeBase = null;
let liveEnumerationAddress = null;
let liveManagerSlot = null;

function managerSlotForEnumeration(enumeration) {
  const callsite = enumeration.add(0x68);
  if (!readable(callsite, 4)) return null;
  const call = callsite.readU32();
  if ((call & 0xfc000000) >>> 0 !== 0x94000000) return null;
  let displacement = call & 0x3ffffff;
  if (displacement & 0x2000000) displacement -= 0x4000000;
  const helper = callsite.add(displacement * 4);
  if (!readable(helper, 12)) return null;
  const adrp = helper.readU32();
  const ldr = helper.add(4).readU32();
  if ((adrp & 0x9f00001f) >>> 0 !== 0x90000008 || (ldr & 0xffc003ff) >>> 0 !== 0xf9400100 || helper.add(8).readU32() !== 0xd65f03c0) return null;
  let immediate = ((adrp >>> 5) & 0x7ffff) * 4 + ((adrp >>> 29) & 3);
  if (immediate & 0x100000) immediate -= 0x200000;
  const slot = helper.and(ptr('0xfffffffffffff000')).add(immediate * 4096).add(((ldr >>> 10) & 4095) * 8);
  if (readable(slot, 8)) return slot;
  // Mach region enumeration can omit specially mapped pages. Test only this
  // metadata-derived pointer slot; NativePointer raises on unreadable memory.
  try { slot.readPointer(); return slot; } catch (_) { return null; }
}

function readable(address, bytes) {
  if (address.isNull() || !Number.isSafeInteger(bytes) || bytes < 0 || bytes > 1048576) return false;
  // Guarded reads validate only a recovered field's bounds. Walking every Mach
  // region for each string is extremely slow in the protected WeChat image.
  try { address.readByteArray(bytes); return true; } catch (_) { return false; }
}
function hexBytes(address, count) {
  return Array.from(new Uint8Array(address.readByteArray(count)), byte => byte.toString(16).padStart(2, '0')).join('');
}
function install(module) {
  if (nativeState.installed || module.name !== 'wechat.dylib' || !module.path.includes('/Contents/Resources/') || module.size < 100000000) return;
  try {
    if (Process.arch !== nativeProfile.arch) throw Error('unsupported_architecture');
    const enumeration = module.base.add(nativeProfile.enumeration.offset);
    const managerSlot = managerSlotForEnumeration(enumeration);
    if (!managerSlot) {
      nativeState.state = 'waiting_for_live_image';
      return;
    }
    for (const name of ['enumeration', 'initialMessages', 'pagedMessages']) {
      const entry = nativeProfile[name];
      const address = module.base.add(entry.offset);
      const executable = Process.findRangeByAddress(address);
      if (!executable || !executable.protection.includes('x')) throw Error('native_module_not_executable');
      if (!readable(address, entry.signature.length / 2) || hexBytes(address, entry.signature.length / 2) !== entry.signature) throw Error('native_signature_mismatch');
    }
    nativeState.installed = true;
    liveNativeBase = module.base;
    liveEnumerationAddress = enumeration;
    liveManagerSlot = managerSlot;
    nativeState.state = 'native_ready';
  } catch (error) {
    nativeState.failure = error.message;
    nativeState.state = 'error';
  }
}

let nativeSupport;
function supportFunctions() {
  if(nativeSupport) return nativeSupport;
  const module=Module.load(globalThis.WECHAT_NATIVE_SUPPORT);
  const exports=new Map(module.enumerateExports().map(entry=>[entry.name,entry.address]));
  const ownExport=name=>{const address=exports.get(name);if(!address)throw Error('native_support_export_missing');return address;};
  nativeSupport={
    selfId:new NativeFunction(ownExport('bridge_self_id'),'pointer',['pointer','pointer'],{exceptions:'steal'}),
    importControl:new NativeFunction(ownExport('bridge_import_control'),'pointer',['pointer','pointer','pointer','pointer','uint'],{exceptions:'steal'}),
    captureVoice:new NativeFunction(ownExport('bridge_capture_recorded_voice'),'pointer',['pointer','pointer'],{exceptions:'steal'}),
    sendVoice:new NativeFunction(ownExport('bridge_send_voice'),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','pointer','uint','uint','bool'],{exceptions:'steal'}),
    ocr:new NativeFunction(ownExport('bridge_extract_image_text'),'pointer',['pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    transcribe:new NativeFunction(ownExport('bridge_start_voice_transcription'),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    transcript:new NativeFunction(ownExport('bridge_voice_transcription_result'),'pointer',['pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    probeProcessor:new NativeFunction(ownExport('bridge_probe_text_processor_v2'),'pointer',['pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    prepareText:new NativeFunction(ownExport('bridge_prepare_text_v2'),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    send:new NativeFunction(ownExport('bridge_send_text_v2'),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    stage:new NativeFunction(ownExport('bridge_last_stage'),'uint',[]),
    hasTask:new NativeFunction(ownExport('bridge_has_task_context'),'bool',['pointer']),
    chats:new NativeFunction(ownExport('bridge_read_chats_v2'),'pointer',['pointer','pointer','pointer','pointer','pointer'],{exceptions:'steal'}),
    cachedAttachment:new NativeFunction(ownExport('bridge_cached_attachment'),'pointer',['pointer','uint64','pointer'],{exceptions:'steal'}),
    cachedMedia:new NativeFunction(ownExport('bridge_cached_media'),'pointer',['pointer'],{exceptions:'steal'}),
    messages:new NativeFunction(ownExport('bridge_read_messages'),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','uint','pointer'],{exceptions:'steal'})};
  return nativeSupport;
}
function nativeEntry(name) {
  if(!liveNativeBase) throw Error('native_module_missing');
  const entry=nativeProfile[name];
  if(!entry) throw Error('native_feature_unavailable');
  const address=liveNativeBase.add(entry.offset);
  if(!readable(address,entry.signature.length/2)||hexBytes(address,entry.signature.length/2)!==entry.signature) throw Error('native_signature_mismatch');
  return address;
}

let migrationHooksInstalled=false;
const migrationState={starts:0,listenerAttempts:0,listenerReady:false};
function installMigrationHooks(){
  if(migrationHooksInstalled)return;
  Interceptor.attach(nativeEntry('migrationStart'),{onEnter(){migrationState.starts++;migrationState.listenerReady=false;}});
  Interceptor.attach(nativeEntry('migrationRoamStart'),{onEnter(){migrationState.listenerAttempts++;},onLeave(result){migrationState.listenerReady=Boolean(result.toInt32()&255);}});
  migrationHooksInstalled=true;
}
let microphoneHooksInstalled=false;
const microphoneState={dictation:{starts:0,finishes:0,active:false},recording:{starts:0,active:false}};
let controlledRecording=null;
function installMicrophoneHooks(){
  if(microphoneHooksInstalled)return;
  nativeEntry('dictationStart');nativeEntry('dictationFinish');nativeEntry('recordingStart');nativeEntry('recordingSend');nativeEntry('recordingBackendStart');
  Interceptor.attach(nativeEntry('dictationStart'),{onEnter(){microphoneState.dictation.starts++;microphoneState.dictation.active=true;}});
  Interceptor.attach(nativeEntry('dictationFinish'),{onEnter(args){microphoneState.dictation.finishes++;microphoneState.dictation.active=false;microphoneState.dictation.finishReason=args[1].toInt32();}});
  Interceptor.attach(nativeEntry('recordingStart'),{onEnter(){microphoneState.recording.starts++;microphoneState.recording.active=true;}});
  Interceptor.attach(nativeEntry('recordingBackendStart'),{onEnter(){if(controlledRecording){microphoneState.recording.starts++;microphoneState.recording.active=true;}}});
  const entry=nativeEntry('recordingSend'),original=new NativeFunction(entry,'void',['pointer','pointer']);
  const replacement=new NativeCallback((context,audio)=>{
    if(!controlledRecording){original(context,audio);return;}
    // API-owned recording is intercepted before native SendVoice queues it.
    // The caller must invoke the separate send endpoint to transmit it.
    microphoneState.recording.active=false;
    try{const value=decodeResult(supportFunctions().captureVoice(context,audio));
      if(value.chatId!==controlledRecording.chatId)throw Error('native_recording_recipient_changed');
      controlledRecording.result=value;controlledRecording.status='recorded';
    }catch(error){controlledRecording.status='failed';controlledRecording.error=error.message;}
  },'void',['pointer','pointer']);
  Interceptor.replace(entry,replacement);microphoneHooksInstalled=true;
}
function accountContext() {
  if(!nativeState.installed) rpc.exports.discoverLiveImage();
  if(!liveManagerSlot) throw Error('native_module_missing');
  const manager=liveManagerSlot.readPointer();
  if(!readable(manager,0x78)) throw Error('native_manager_unavailable');
  const vtable=manager.readPointer();
  if(!readable(vtable.add(0x68),8)) throw Error('native_manager_unavailable');
  return {manager,getter:vtable.add(0x68).readPointer(),available:!manager.add(0x68).readPointer().isNull()};
}
function decodeResult(address) {
  const value=JSON.parse(address.readUtf8String());
  if(value.error) throw Error(value.error);
  return value;
}
function nameFunctions() {
  const addresses=[nativeEntry('accountServices'),nativeEntry('contactService'),nativeEntry('contactDisplayName')];
  const table=Memory.alloc(addresses.length*Process.pointerSize);
  addresses.forEach((address,i)=>table.add(i*Process.pointerSize).writePointer(address));
  return table;
}
function recentFunctions() {
  const addresses=[nativeEntry('accountServices'),nativeEntry('messageService'),nativeEntry('initialMessages'),nativeEntry('sessionService'),liveNativeBase.add(nativeProfile.sessionServiceMarkerOffset),nativeEntry('copySessions'),nativeEntry('sessionDestructor'),nativeEntry('isMinimizedSession'),nativeEntry('isPinnedSession')];
  const table=Memory.alloc(addresses.length*Process.pointerSize);
  addresses.forEach((address,i)=>table.add(i*Process.pointerSize).writePointer(address));
  return table;
}
function refreshConversations(withNames=false) {
  const context=accountContext();
  if(!context.available) {nativeState.chats=[];nativeState.connected=false;throw Error('native_account_not_ready');}
  const value=decodeResult(supportFunctions().chats(nativeEntry('enumeration'),context.getter,context.manager,withNames?nameFunctions():ptr(0),withNames?recentFunctions():ptr(0)));
  nativeState.chats=value.items;nativeState.enumerationObserved=true;nativeState.connected=true;nativeState.failure=null;
  return value;
}
function nativeMessages(chatId,limit,mediaMessageId=null) {
  if(mediaMessageId!==null&&!/^\d{1,20}$/.test(mediaMessageId))throw Error('native_invalid_media_id');
  if(typeof chatId!=='string'||!chatId||chatId.length>1024||/[\x00-\x1f\x7f]/.test(chatId))throw Error('invalid_chat_id');
  if(!Number.isInteger(limit)||limit<1||limit>100)throw Error('invalid_limit');
  refreshConversations();
  if(!nativeState.chats.some(chat=>chat.id===chatId))throw Error('native_chat_not_found');
  const context=accountContext();
  if(nativePendingRead) throw Error('native_bridge_busy');
  const services=nativeEntry('accountServices'),service=nativeEntry('messageService'),initial=Memory.alloc(8*Process.pointerSize);
  [nativeEntry('initialMessages'),nativeEntry('messageUiConversion'),nativeEntry('messageUiDestructor'),nativeEntry('accountServices'),nativeEntry('contactService'),nativeEntry('contactDisplayName'),nativeEntry('voiceAudioPath'),nativeEntry('voiceDataGetter')].forEach((address,i)=>initial.add(i*Process.pointerSize).writePointer(address));
  const chat=Memory.allocUtf8String(chatId);const support=supportFunctions();
  return inNativeTask(()=>{
    const value=decodeResult(support.messages(context.getter,context.manager,services,service,initial,chat,limit,mediaMessageId===null?ptr(0):Memory.allocUtf8String(mediaMessageId)));
    nativeState.historyVerified=nativeState.historyVerified||value.items.some(item=>item.content?.length>0);
    for(const item of value.items){if([3,34,43,47,49,62].includes(item.type))for(const key of [item.id,item.serverId])if(/^\d{12,20}$/.test(key))observedMediaHashes.add(key);for(const match of item.content.matchAll(/(?:md5|voicemd5)=["']([a-f0-9]{32})["']/gi))observedMediaHashes.add(match[1].toLowerCase());for(const hash of Object.values(item.nativeMediaFields??{}))if(/^[a-zA-Z0-9_.-]{8,128}$/.test(hash)&&!hash.includes('..'))observedMediaHashes.add(hash);}
    nativeState.messageReadsObserved++;
    return value;
  });
}
function nativeSendText(chatId,text,prepare=false) {
  if(typeof chatId!=='string'||!chatId||chatId.length>1024||/[\x00-\x1f\x7f]/.test(chatId))throw Error('invalid_chat_id');
  if(['@placeholder_foldgroup','brandsessionholder','brandservicesessionholder'].includes(chatId))throw Error('native_invalid_recipient');
  if(typeof text!=='string'||!text.trim()||text.length>4096||text.includes('\0'))throw Error('invalid_text');
  refreshConversations();
  if(!nativeState.chats.some(chat=>chat.id===chatId))throw Error('native_chat_not_found');
  const context=accountContext(),functions=Memory.alloc(4*Process.pointerSize);
  ['makeNativeUuid','makeUiSendInfo','sendGroupMessages','sendResultDestructor'].forEach((name,i)=>functions.add(i*Process.pointerSize).writePointer(nativeEntry(name)));
  const chat=Memory.allocUtf8String(chatId),content=Memory.allocUtf8String(text),services=nativeEntry('accountServices'),service=nativeEntry('messageService');
  // Native UiSendInfo was verified with a server-acknowledged File Transfer test.
  return inNativeTask(()=>decodeResult((prepare?supportFunctions().prepareText:supportFunctions().send)(context.getter,context.manager,services,service,functions,chat,content)));

}
function inNativeTask(work) {
  if(nativePendingRead) throw Error('native_bridge_busy');
  const guard=nativeEntry('taskContextGuard'),support=supportFunctions();
  return new Promise((resolve,reject)=>{
    let listener,timer,finished=false;
    const finish=(error,value)=>{
      if(finished)return;finished=true;clearTimeout(timer);nativePendingRead=false;
      listener?.detach();
      if(error)reject(error);else resolve(value);
    };
    nativePendingRead=true;
    timer=setTimeout(()=>finish(Error('native_task_context_unavailable')),12000);
    try {listener=Interceptor.attach(guard,{onEnter(){
      if(finished||!nativePendingRead||nativeTaskReadActive)return;
      if(!support.hasTask(liveNativeBase.add(nativeProfile.taskContextTlsOffset)))return;
      nativeTaskReadActive=true;
      try{finish(null,work());}
      catch(error){lastNativeFault={address:error.address?.toString(),offset:error.address&&liveNativeBase?error.address.sub(liveNativeBase).toString():null};finish(error);}
      finally{nativeTaskReadActive=false;}
    }});}catch(error){finish(error);}
  });
}
const observedMediaHashes=new Set();
let nativePendingRead=false,nativeTaskReadActive=false;
let lastNativeFault=null;
const observer = Process.attachModuleObserver({ onAdded: install });
rpc.exports = {
  disposeGui() { return globalThis.WECHAT_GUI_LIFECYCLE?.dispose?.(); },
  startupStatus() {
    let applicationRunning = null;
    try {
      const app = Module.getGlobalExportByName('NSApp').readPointer();
      if (!app.isNull()) {
        const register = new NativeFunction(Module.getGlobalExportByName('sel_registerName'), 'pointer', ['pointer']);
        const message = new NativeFunction(Module.getGlobalExportByName('objc_msgSend'), 'bool', ['pointer', 'pointer']);
        applicationRunning = message(app, register(Memory.allocUtf8String('isRunning')));
      }
    } catch (_) {}
    return { pid:Process.id, executable:Process.mainModule.path, applicationRunning, largeModules: Process.enumerateModules()
      .filter(module => module.size > 50000000 && !module.path.startsWith('/System/') && !module.path.startsWith('/usr/lib/'))
      .map(module => ({ name: module.name, path: module.path, size: module.size })) };
  },
  discoverLiveImage() {
    if (nativeState.installed) return { installed: true, candidatesChecked: 0 };
    let bytesChecked = 0;
    let candidatesChecked = 0;
    const ranges = Process.enumerateRanges({ protection: 'r-x', coalesce: true })
      .filter(range => {
        if (range.protection !== 'r-x' || range.size < 128 || range.size > 536870912) return false;
        const owner = Process.findModuleByAddress(range.base);
        return owner === null || (!owner.path.startsWith('/System/') && !owner.path.startsWith('/usr/lib/'));
      })
      .sort((a, b) => b.size - a.size);
    for (const range of ranges.slice(0, 512)) {
      if (bytesChecked + range.size > 805306368) break;
      bytesChecked += range.size;
      for (const hit of Memory.scanSync(range.base, range.size, nativeProfile.enumeration.scanPattern)) {
        candidatesChecked++;
        const base = hit.address.sub(nativeProfile.enumeration.offset);
        if (!managerSlotForEnumeration(hit.address)) continue;
        install({ name: 'wechat.dylib', path: '/Contents/Resources/live-native-image', size: range.size, base });
        if (nativeState.installed) return { installed: true, candidatesChecked, bytesChecked };
      }
    }
    return { installed: false, candidatesChecked, bytesChecked };
  },
  status() {
    let accountAvailable = false;
    try {accountAvailable=accountContext().available;nativeState.failure=null;} catch(error) {nativeState.failure=error.message;}
    return { protocol: nativeState.protocol, state: nativeState.state, wechatVersion:nativeProfile.version, build:nativeProfile.build,
      installationNoticeSuppression:{installed:Boolean(globalThis.WECHAT_INSTALL_NOTICE?.installed),suppressed:globalThis.WECHAT_INSTALL_NOTICE?.suppressed??0},
      guiLifecycle:{installed:Boolean(globalThis.WECHAT_GUI_LIFECYCLE?.installed), primaryVisible:globalThis.WECHAT_GUI_LIFECYCLE?.primaryVisible??0, seen:Boolean(globalThis.WECHAT_GUI_LIFECYCLE?.seen)},
      connected: accountAvailable, accountAvailable, installed: nativeState.installed,
      enumerationObserved: nativeState.enumerationObserved,
      chatCount: nativeState.chats.length, messageReadsObserved: nativeState.messageReadsObserved,
      failure: nativeState.failure, lastNativeFault, lastNativeStage:nativeSupport?.stage(), messagesImplemented: true, historyVerified: Boolean(nativeState.historyVerified), sendsImplemented: true };
  },
  probeTextProcessor() {const context=accountContext();const addresses=[nativeEntry('accountServices'),nativeEntry('messageService'),nativeEntry('sendProcessor')];return inNativeTask(()=>{const value=decodeResult(supportFunctions().probeProcessor(context.getter,context.manager,...addresses));value.processorVtableOffset=ptr(value.processorVtable).sub(liveNativeBase).toString();delete value.processorVtable;return value;});},
  voiceData(chatId,id){return nativeMessages(chatId,100,id);},
  importControl(action){accountContext();installMigrationHooks();if(![0,1,2].includes(action))throw Error('invalid_input');const result=decodeResult(supportFunctions().importControl(nativeEntry('migrationWindow'),nativeEntry('migrationStopServices'),nativeEntry('migrationCancelTransfer'),nativeEntry('migrationBusyKind'),action));return {...result,...migrationState};},
  migrationStatus(){return rpc.exports.importControl(0);},
  selfId(){const context=accountContext();return decodeResult(supportFunctions().selfId(context.getter,context.manager));},
  microphoneStatus(){accountContext();installMicrophoneHooks();return {dictation:{...microphoneState.dictation},recording:{...microphoneState.recording,status:controlledRecording?.status??'idle'}};},
  armRecording(chatId){accountContext();installMicrophoneHooks();if(controlledRecording)throw Error('native_bridge_busy');refreshConversations();if(!nativeState.chats.some(chat=>chat.id===chatId))throw Error('native_chat_not_found');controlledRecording={chatId,status:'armed'};return {status:'armed'};},
  recordingResult(){if(!controlledRecording)throw Error('native_recording_unavailable');return {status:controlledRecording.status,...(controlledRecording.result??{}),...(controlledRecording.error?{error:controlledRecording.error}:{})};},
  discardRecording(){controlledRecording=null;microphoneState.recording.active=false;return {status:'discarded'};},
  sendVoice(chatId,dataBase64,durationMs,prepare=false){
    if(typeof chatId!=='string'||!chatId||chatId.length>1024||/[\x00-\x1f\x7f]/.test(chatId))throw Error('invalid_chat_id');
    if(['@placeholder_foldgroup','brandsessionholder','brandservicesessionholder'].includes(chatId))throw Error('native_invalid_recipient');
    refreshConversations();if(!nativeState.chats.some(chat=>chat.id===chatId))throw Error('native_chat_not_found');
    if(typeof dataBase64!=='string'||dataBase64.length>1398104||dataBase64.length%4||!/^[A-Za-z0-9+/]+={0,2}$/.test(dataBase64)||!Number.isInteger(durationMs)||durationMs<1000||durationMs>60000)throw Error('invalid_voice');
    const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/',values=[];let acc=0,bits=0;
    for(const c of dataBase64){if(c==='=')break;acc=(acc<<6)|alphabet.indexOf(c);bits+=6;if(bits>=8){bits-=8;values.push((acc>>>bits)&255);}}
    const bytes=new Uint8Array(values);if(bytes.length<10||bytes.length>1048576)throw Error('invalid_voice');
    const buffer=Memory.alloc(bytes.length);buffer.writeByteArray(bytes);
    const context=accountContext(),functions=Memory.alloc(4*Process.pointerSize);
    ['makeNativeUuid','makeVoiceSendInfo','sendGroupMessages','sendResultDestructor'].forEach((name,i)=>functions.add(i*Process.pointerSize).writePointer(nativeEntry(name)));
    return inNativeTask(()=>decodeResult(supportFunctions().sendVoice(context.getter,context.manager,nativeEntry('accountServices'),nativeEntry('messageService'),functions,Memory.allocUtf8String(chatId),buffer,bytes.length,durationMs,prepare?1:0)));
  },
  extractImageText(dataBase64){
    if(typeof dataBase64!=='string'||dataBase64.length<4||dataBase64.length>11184812||dataBase64.length%4||!/^[A-Za-z0-9+/]+={0,2}$/.test(dataBase64))throw Error('native_invalid_ocr_image');
    const context=accountContext(),functions=Memory.alloc(3*Process.pointerSize);
    ['ocrService','ocrInitialize','ocrLocalRecognition'].forEach((name,i)=>functions.add(i*Process.pointerSize).writePointer(nativeEntry(name)));
    const services=nativeEntry('accountServices'),image=Memory.allocUtf8String(dataBase64);
    return inNativeTask(()=>decodeResult(supportFunctions().ocr(context.getter,context.manager,services,functions,image)));
  },
  voiceTranscriptionResult(chatId,id){
    if(typeof chatId!=='string'||!chatId||chatId.length>1024||/[\x00-\x1f\x7f]/.test(chatId)||!/^\d{1,20}$/.test(id))throw Error('invalid_chat_id');
    const context=accountContext();return decodeResult(supportFunctions().transcript(context.getter,context.manager,Memory.allocUtf8String(chatId),Memory.allocUtf8String(id)));
  },
  transcribeVoice(chatId,id){
    if(typeof chatId!=='string'||!chatId||chatId.length>1024||/[\x00-\x1f\x7f]/.test(chatId)||!/^\d{1,20}$/.test(id))throw Error('invalid_chat_id');
    refreshConversations();if(!nativeState.chats.some(chat=>chat.id===chatId))throw Error('native_chat_not_found');
    const context=accountContext(),functions=Memory.alloc(7*Process.pointerSize);
    ['initialMessages','messageUiConversion','messageUiDestructor','voiceFrontRegistry'].forEach((name,i)=>functions.add(i*Process.pointerSize).writePointer(nativeEntry(name)));
    functions.add(4*Process.pointerSize).writePointer(liveNativeBase.add(nativeProfile.voiceFrontDescriptorOffset));
    functions.add(5*Process.pointerSize).writePointer(nativeEntry('voiceTranscriptionStart'));
    functions.add(6*Process.pointerSize).writePointer(nativeEntry('voiceTranscriptionAttach'));
    const services=nativeEntry('accountServices'),service=nativeEntry('messageService'),chat=Memory.allocUtf8String(chatId),message=Memory.allocUtf8String(id);
    return inNativeTask(()=>decodeResult(supportFunctions().transcribe(context.getter,context.manager,services,service,functions,chat,message)));
  },
  async attachmentData(chatId,id) {
      const value=await nativeMessages(chatId,100),message=value.items.find(item=>item.id===id);
      if(!message||message.type!==49||!/<appmsg[\s>]/.test(message.content)||!/<type>6<\/type>/.test(message.content))throw Error('invalid_media_type');
      const name=message.nativeMediaFields?.['344'],match=message.content.match(/<totallen>(\d+)<\/totallen>/);
      const size=match?Number(match[1]):0;if(!name||!Number.isSafeInteger(size)||size<1||size>128*1024*1024)throw Error('native_media_not_cached');
      const context=accountContext(),support=supportFunctions(),self=decodeResult(support.selfId(context.getter,context.manager)).id;
      return decodeResult(support.cachedAttachment(Memory.allocUtf8String(name),size,Memory.allocUtf8String(self)));
  },
  cachedMedia(hash) {accountContext();if(!observedMediaHashes.has(hash))throw Error('native_invalid_media_id');return decodeResult(supportFunctions().cachedMedia(Memory.allocUtf8String(hash)));},
  prepareText(chatId,text) {return nativeSendText(chatId,text,true);},
  sendText(chatId,text) {return nativeSendText(chatId,text);},
  listMessages(chatId,limit) {return nativeMessages(chatId,limit);},
  listChats() {
    if(!accountContext().available) throw Error('native_account_not_ready');
    return inNativeTask(()=>refreshConversations(true));
  },
};
