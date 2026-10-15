// Exact-build Windows instrumentation. Database access stays in the owning process.
const profile = globalThis.WECHAT_WINDOWS_PROFILE;
const module = Process.getModuleByName('Weixin.dll');
if (Process.arch !== profile.arch) throw Error('unsupported_architecture');
const verifiedEntries=new Map();
function entry(spec) {
  const key=spec.offset+':'+spec.signature;
  if(verifiedEntries.has(key))return verifiedEntries.get(key);
  const p = module.base.add(spec.offset);
  const actual = Array.from(new Uint8Array(p.readByteArray(spec.signature.length / 2)), b => b.toString(16).padStart(2, '0')).join('');
  if (actual !== spec.signature) throw Error('native_signature_mismatch');
  const range = Process.findRangeByAddress(p);
  if (!range || !range.protection.includes('x')) throw Error('native_signature_mismatch');
  verifiedEntries.set(key,p);
  return p;
}
const signatures = {
  libversion:['pointer',[]],prepare_v2:['int',['pointer','pointer','int','pointer','pointer']],
  step:['int',['pointer']],finalize:['int',['pointer']],column_count:['int',['pointer']],
  column_name:['pointer',['pointer','int']],column_type:['int',['pointer','int']],
  column_int64:['int64',['pointer','int']],column_double:['double',['pointer','int']],
  column_text:['pointer',['pointer','int']],column_blob:['pointer',['pointer','int']],
  column_bytes:['int',['pointer','int']],bind_text:['int',['pointer','int','pointer','int','pointer']],
  bind_int64:['int',['pointer','int','int64']],bind_null:['int',['pointer','int']],
  db_filename:['pointer',['pointer','pointer']],db_mutex:['pointer',['pointer']],
  mutex_try:['int',['pointer']],mutex_leave:['void',['pointer']],vfs_find:['pointer',['pointer']],
  stmt_readonly:['int',['pointer']]
};
const api = {};
for (const [name, shape] of Object.entries(signatures)) api[name] = new NativeFunction(entry(profile.sqlite[name]), ...shape, {exceptions:'steal'});
const main = Memory.allocUtf8String('main'), handles = new Map(), hooks = [];
let scanning = false, scanned = false, scanAccount = null;
function remember(db) {
  try {
    if (handles.has(db.toString())) return;
    const path = api.db_filename(db, main);
    if (path.isNull()) return;
    const full = path.readUtf8String();
    const normalized=full.replace(/\\/g,'/');
    const account=selfId();
    if(!normalized.split('/').some(part=>part===account||part.startsWith(account+'_')))return;
    const name=normalized.split('/').pop().toLowerCase();
    if (/^(session|contact|message_\d+|biz_message_\d+|hardlink)\.db$/.test(name) && handles.size < 256) handles.set(db.toString(), {db,name,full,account});
  } catch (_) {}
}
for (const name of ['prepare_v2','prepare_v3']) hooks.push(Interceptor.attach(entry(profile.sqlite[name]), {onEnter(args) { remember(args[0]); }}));
for (const name of ['close','close_v2']) hooks.push(Interceptor.attach(entry(profile.sqlite[name]), {onEnter(args) {this.key=args[0].toString();},onLeave(){handles.delete(this.key);}}));
function discover() {
  let account;
  try{account=selfId();}catch(_){return;}
  if(scanAccount!==account){scanned=false;scanAccount=account;handles.clear();}
  if (scanning || scanned) return;
  scanning = true;
  try {
    const vfs = api.vfs_find(ptr(0));
    if (vfs.isNull()) return;
    let inspected = 0;
    for (const r of Process.enumerateRanges({protection:'rw-',coalesce:true})) {
      if (r.size > 64 * 1024 * 1024 || inspected + r.size > 512 * 1024 * 1024) continue;
      inspected += r.size;
      let hits;
      try { hits = Memory.scanSync(r.base,r.size,vfs.toMatchPattern()); } catch (_) { continue; }
      for (const hit of hits) {
        try {
          const db = hit.address, aDb = db.add(32).readPointer(), nDb = db.add(40).readU32();
          if (nDb < 2 || nDb > 8 || aDb.isNull() || aDb.readPointer().readUtf8String(4) !== 'main') continue;
          remember(db);
        } catch (_) {}
      }
    }
    scanned = handles.size > 0;
  } finally { scanning = false; }
}
// WCDB also opens NOMUTEX handles. Guard their public API entries with a
// recursive, per-connection Windows mutex while the bridge borrows a handle.
const kernel=Process.getModuleByName('kernel32.dll');
const createMutex=new NativeFunction(kernel.getExportByName('CreateMutexW'),'pointer',['pointer','int','pointer']);
const waitMutex=new NativeFunction(kernel.getExportByName('WaitForSingleObject'),'uint',['pointer','uint']);
const leaveMutex=new NativeFunction(kernel.getExportByName('ReleaseMutex'),'int',['pointer']);
const closeHandle=new NativeFunction(kernel.getExportByName('CloseHandle'),'int',['pointer']);
const connectionLocks=new Map();
let activeGuards=0,disposing=false;
function connectionLock(db) {
  const key=db.toString();
  if(!connectionLocks.has(key)) {
    const handle=createMutex(ptr(0),0,ptr(0));
    if(handle.isNull())throw Error('native_database_lock_failed');
    connectionLocks.set(key,handle);
  }
  return connectionLocks.get(key);
}
const guardedAddresses=new Set();
for(const spec of Object.values(profile.sqliteGuards)) {
  const address=entry(spec);
  if(guardedAddresses.has(address.toString()))continue;
  guardedAddresses.add(address.toString());
  hooks.push(Interceptor.attach(address,{
    onEnter(args) {
      if(disposing)return;
      try {
        const db=spec.statement?args[0].readPointer():args[0];
        if(!handles.has(db.toString())||!api.db_mutex(db).isNull())return;
        const lock=connectionLock(db);activeGuards++;
        const result=waitMutex(lock,0xffffffff);
        if(result!==0&&result!==0x80){activeGuards--;return;}
        this.connectionLock=lock;
      } catch(_) {}
    },
    onLeave() {if(this.connectionLock){leaveMutex(this.connectionLock);activeGuards--;}}
  }));
}
function query(name, sql, parameters, maximum) {
  if (typeof sql !== 'string' || !/^\s*SELECT\b/i.test(sql) || sql.length > 16384 || !Array.isArray(parameters) || parameters.length > 10 || !Number.isInteger(maximum) || maximum < 1 || maximum > 20000) throw Error('invalid_input');
  const account=selfId();
  discover();
  const candidates = [...handles.values()].filter(h => h.name === name && h.account === account);
  if (!candidates.length) throw Error('native_account_not_ready');
  for (const {db} of candidates) {
    const mutex = api.db_mutex(db);
    const external=mutex.isNull()?connectionLock(db):null;
    if(external){const result=waitMutex(external,0);if(result!==0&&result!==0x80)continue;}
    else if(api.mutex_try(mutex)!==0)continue;
    let statement = ptr(0);
    try {
      const slot = Memory.alloc(8), tail = Memory.alloc(8), input = Memory.allocUtf8String(sql);
      slot.writePointer(ptr(0));tail.writePointer(ptr(0));
      if (api.prepare_v2(db,input,-1,slot,tail) !== 0) throw Error('native_database_query_failed');
      statement = slot.readPointer();
      if (statement.isNull() || !api.stmt_readonly(statement) || (!tail.readPointer().isNull() && tail.readPointer().readUtf8String().trim())) throw Error('native_database_query_failed');
      const values=[];
      for (let i=0;i<parameters.length;i++) {
        let code;
        if (parameters[i] === null) code=api.bind_null(statement,i+1);
        else if (typeof parameters[i] === 'string' && parameters[i].length <= 4096) { const value=Memory.allocUtf8String(parameters[i]);values.push(value);code=api.bind_text(statement,i+1,value,-1,ptr(0)); }
        else if (Number.isSafeInteger(parameters[i])) code=api.bind_int64(statement,i+1,int64(parameters[i]));
        else throw Error('invalid_input');
        if (code !== 0) throw Error('native_database_query_failed');
      }
      const count=api.column_count(statement);
      if (count < 1 || count > 40) throw Error('native_result_too_large');
      const columns=Array.from({length:count},(_,i)=>api.column_name(statement,i).readUtf8String());
      const rows=[];let total=0,rc;
      while ((rc=api.step(statement))===100) {
        if (rows.length>=maximum) throw Error('native_result_too_large');
        const row={};
        for(let i=0;i<count;i++) {
          const type=api.column_type(statement,i);
          if(type===5) row[columns[i]]=null;
          else if(type===1) row[columns[i]]=api.column_int64(statement,i).toString();
          else if(type===2) row[columns[i]]=api.column_double(statement,i);
          else {
            const size=api.column_bytes(statement,i);total+=size;
            if(size<0||size>2*1024*1024||total>16*1024*1024) throw Error('native_result_too_large');
            if(type===4) row[columns[i]]={hex:Array.from(new Uint8Array(api.column_blob(statement,i).readByteArray(size)),b=>b.toString(16).padStart(2,'0')).join('')};
            else row[columns[i]]=size?api.column_text(statement,i).readUtf8String(size):'';
          }
        }
        rows.push(row);
      }
      if(rc!==101) throw Error('native_database_query_failed');
      if(selfId()!==account)throw Error('native_account_changed');
      return rows;
    } finally {
      if(!statement.isNull())api.finalize(statement);
      if(external)leaveMutex(external);else api.mutex_leave(mutex);
    }
  }
  throw Error('native_database_busy');
}
function selfId() {
  const manager = new NativeFunction(entry(profile.manager),'pointer',[])();
  if(manager.isNull()||manager.add(0x70).readPointer().isNull())throw Error('native_account_not_ready');
  const method = manager.readPointer().add(0x20).readPointer();
  // This exact-build method returns a const reference to the manager's account ID.
  if (profile.selfId && !method.equals(entry(profile.selfId))) throw Error('native_signature_mismatch');
  if (!profile.selfId) throw Error('native_feature_unavailable');
  const value = new NativeFunction(method,'pointer',['pointer'])(manager);
  const size=value.add(16).readU64().toNumber(),capacity=value.add(24).readU64().toNumber();
  if(size<1||size>1024||capacity<size||capacity>1048576)throw Error('native_account_not_ready');
  return (capacity<16?value:value.readPointer()).readUtf8String(size);
}
const user32=Process.getModuleByName('user32.dll');
const windowApi={};
for(const [name,ret,args] of [
  ['GetForegroundWindow','pointer',[]],['ShowWindow','int',['pointer','int']],['SetForegroundWindow','int',['pointer']],
  ['EnumWindows','int',['pointer','pointer']],['GetWindowThreadProcessId','uint',['pointer','pointer']],
  ['GetWindow','pointer',['pointer','uint']],['IsWindowVisible','int',['pointer']],
  ['GetWindowTextW','int',['pointer','pointer','int']],['GetClassNameW','int',['pointer','pointer','int']]
])windowApi[name]=new NativeFunction(user32.getExportByName(name),ret,args);
let sawGui=false,missingGuiAt=null,guiStopped=false,primaryWindow=null;
function guiWindows(visibleOnly=true) {
  const found=[],pid=Memory.alloc(4),title=Memory.alloc(512),name=Memory.alloc(512);
  const visit=new NativeCallback(hwnd=>{
    windowApi.GetWindowThreadProcessId(hwnd,pid);
    if(pid.readU32()!==Process.id||!windowApi.GetWindow(hwnd,4).isNull()||(visibleOnly&&!windowApi.IsWindowVisible(hwnd)))return 1;
    const titleLength=windowApi.GetWindowTextW(hwnd,title,256),classLength=windowApi.GetClassNameW(hwnd,name,256);
    if(titleLength&&classLength&&['Weixin','WeChat','微信'].includes(title.readUtf16String(titleLength).trim())&&name.readUtf16String(classLength).startsWith('Qt'))found.push(hwnd);
    return 1;
  },'int',['pointer','pointer']);
  windowApi.EnumWindows(visit,ptr(0));
  return found;
}
function selectWindow(hwnd){
  if(!primaryWindow||!primaryWindow.equals(hwnd)){
    primaryWindow=hwnd;send({event:'window_changed',window:hwnd.toString()});
  }
}
function guiStatus(){
  const windows=guiWindows(),foreground=windowApi.GetForegroundWindow();
  const active=windows.find(hwnd=>hwnd.equals(foreground));
  if(!primaryWindow&&windows.length)selectWindow(active||windows[0]);
  if(primaryWindow&&!windows.some(hwnd=>hwnd.equals(primaryWindow))&&active)selectWindow(active);
  const visible=Boolean(primaryWindow&&windows.some(hwnd=>hwnd.equals(primaryWindow)));
  if(visible){sawGui=true;missingGuiAt=null;}
  return {visible,observed:sawGui};
}
const guiTimer=setInterval(()=>{
  if(disposing||guiStopped)return;
  try{
    if(guiStatus().visible||!sawGui)return;
    missingGuiAt=missingGuiAt||Date.now();
    if(Date.now()-missingGuiAt>=3000){guiStopped=true;send({event:'stopped',reason:'gui_closed'});}
  }catch(_){guiStopped=true;send({event:'stopped',reason:'bridge_monitor_failed'});}
},200);
function activeDatabases(){
  discover();
  try{const account=selfId();return [...new Set([...handles.values()].filter(h=>h.account===account).map(h=>h.name))];}
  catch(_){return [];}
}
rpc.exports = {
  status(){const databases=activeDatabases();return {connected:databases.length>0,protocol:'wechat-windows',state:databases.length?'native_ready':'waiting_for_login',version:profile.version,sqlite:api.libversion().readUtf8String(),databaseCount:databases.length,gui:guiStatus()};},
  restoreGui(value){
    if(typeof value!=='string'||!/^0x[0-9a-fA-F]{1,16}$/.test(value))throw Error('native_gui_not_ready');
    const window=guiWindows(false).find(hwnd=>hwnd.equals(ptr(value)));
    if(!window)throw Error('native_gui_not_ready');
    selectWindow(window);windowApi.ShowWindow(window,9);windowApi.SetForegroundWindow(window);
    return guiStatus();
  },
  databases:activeDatabases,
  query,
  selfId(){return {id:selfId()};},
  async dispose(){disposing=true;clearInterval(guiTimer);while(activeGuards)await new Promise(resolve=>setTimeout(resolve,10));for(const hook of hooks)hook.detach();hooks.length=0;for(const lock of connectionLocks.values())closeHandle(lock);connectionLocks.clear();handles.clear();}
};

const sendProfile = profile.send;
const makeText = new NativeFunction(entry(sendProfile.factory),'pointer',['pointer'],{exceptions:'steal'});
const releaseShared = new NativeFunction(entry(sendProfile.release),'void',['pointer'],{exceptions:'steal'});
const allocate = new NativeFunction(entry(sendProfile.allocate),'pointer',['uint64'],{exceptions:'steal'});
const makeContext = new NativeFunction(entry(sendProfile.contextConstructor),'pointer',['pointer','pointer','pointer','pointer','pointer','uint64'],{exceptions:'steal'});
const dispatch = new NativeFunction(entry(sendProfile.dispatch),'void',['pointer','pointer'],{exceptions:'steal'});
function readString(value, maximum) {
  const length=value.add(16).readU64().toNumber(),capacity=value.add(24).readU64().toNumber();
  if(!Number.isSafeInteger(length)||length<0||length>maximum||capacity<length||capacity>16777216)throw Error('native_send_layout_unverified');
  return length?(capacity<16?value:value.readPointer()).readUtf8String(length):'';
}
function assignEmptyString(value, text) {
  if(readString(value,65536)!=='')throw Error('native_send_layout_unverified');
  const encoded=unescape(encodeURIComponent(text));
  const bytes=Uint8Array.from(encoded,c=>c.charCodeAt(0)),length=bytes.length;
  let data=value,capacity=15;
  if(length>15) {
    const size=length+1;
    data=allocate(size>=4096?size+39:size);
    if(data.isNull())throw Error('native_send_allocation_failed');
    if(size>=4096){const original=data;data=data.add(39).and(ptr('0xffffffffffffffe0'));data.sub(8).writePointer(original);}
    capacity=length;
    value.writePointer(data);
  }
  data.writeByteArray(bytes);data.add(length).writeU8(0);
  value.add(16).writeU64(length);value.add(24).writeU64(capacity);
}
// Match the native service result to our UUID, rather than treating dispatch as acceptance.
const pendingSends=new Map();
const typeGetter=new NativeFunction(entry(sendProfile.typeGetter),'uint',['pointer']);
const subtypeGetter=new NativeFunction(entry(sendProfile.subtypeGetter),'uint',['pointer']);
hooks.push(Interceptor.attach(entry(sendProfile.service),{
  onEnter(args){
    try{
      const vector=args[2],start=vector.readPointer(),end=vector.add(8).readPointer();
      if(end.sub(start).toInt32()!==16)return;
      const info=start.readPointer(),id=readString(info.add(sendProfile.uuid),128);
      if(pendingSends.has(id)){this.id=id;this.result=args[1];}
    }catch(_){}
  },
  onLeave(){
    if(!this.id)return;
    let accepted=false;
    try{accepted=this.result.readU8()===1;}catch(_){}
    const pending=pendingSends.get(this.id);
    if(pending){pendingSends.delete(this.id);setImmediate(()=>pending(accepted));}
  }
}));
let sendSequence=0;
async function textOperation(chatId,text,send) {
  if(typeof chatId!=='string'||chatId.length<1||chatId.length>1024||typeof text!=='string'||!text.trim()||text.length>4096||text.includes('\0')||chatId.includes('\0'))throw Error('invalid_input');
  const account=selfId();
  if(query('session.db','SELECT username FROM SessionTable WHERE username=?',[chatId],1).length!==1)throw Error('native_chat_not_found');
  const owned=Memory.alloc(16);owned.writePointer(ptr(0));owned.add(8).writePointer(ptr(0));
  let context=null,submitted=false;
  try {
    makeText(owned);
    const info=owned.readPointer();
    if(info.isNull())throw Error('native_send_allocation_failed');
    assignEmptyString(info.add(sendProfile.recipient),chatId);
    assignEmptyString(info.add(sendProfile.content),text);
    info.add(sendProfile.type).writeU32(1);
    info.add(sendProfile.length).writeU64(unescape(encodeURIComponent(text)).length);
    const requestId=readString(info.add(sendProfile.uuid),128);
    if(typeGetter(info)!==1||subtypeGetter(info)!==0||!info.readPointer().add(8).readPointer().equals(entry(sendProfile.typeGetter))||!/^[0-9a-fA-F-]{32,38}$/.test(requestId)||readString(info.add(sendProfile.recipient),1024)!==chatId||readString(info.add(sendProfile.content),65536)!==text)throw Error('native_send_layout_unverified');
    if(!send)return {source:'native',prepared:true,queued:false,receiverVerified:true,requestId,requestKind:'windows_native_task'};
    if(selfId()!==account)throw Error('native_account_changed');
    const group=Memory.alloc(40);group.writePointer(module.base.add(sendProfile.groupVtable));
    group.add(8).writePointer(owned);group.add(16).writePointer(owned.add(16));group.add(24).writePointer(owned.add(16));group.add(32).writeU64(1);
    context=Memory.alloc(0xe8);context.writeByteArray(new Uint8Array(0xe8));
    const success=Memory.alloc(64),failure=Memory.alloc(64),done=Memory.alloc(64),empty=Memory.alloc(16);
    for(const p of [success,failure,done])p.writeByteArray(new Uint8Array(64));
    empty.writePointer(ptr(0));empty.add(8).writePointer(ptr(0));
    makeContext(context,success,failure,done,empty,uint64(Date.now()*1000+(++sendSequence%1000)));
    let finish,timer;
    const completion=new Promise(resolve=>{finish=resolve;});
    pendingSends.set(requestId,finish);
    timer=setTimeout(()=>{if(pendingSends.delete(requestId))finish(null);},10000);
    try{
      submitted=true;
      dispatch(group,context);
      const accepted=await completion;
      if(accepted===null)throw Error('native_send_outcome_unknown');
      if(!accepted)throw Error('native_send_rejected');
    }finally{clearTimeout(timer);pendingSends.delete(requestId);}
    return {source:'native',queued:true,receiverVerified:true,requestId,requestKind:'windows_native_task'};
  } catch(error) {
    if(submitted&&error.message!=='native_send_rejected')throw Error('native_send_outcome_unknown');
    throw error;
  } finally {
    if(context){releaseShared(context);releaseShared(context.add(0xd8));}
    releaseShared(owned);
  }
}
rpc.exports.prepareText=(chat,text)=>textOperation(chat,text,false);
rpc.exports.sendText=(chat,text)=>textOperation(chat,text,true);
send({event:'agent_ready'});
