// Native consent for one explicitly requested Channels OAuth operation.
// Arm from the verified Channels document; accept only a newly created native
// Channels snsapi_login window during that operation. Native and loopback nonces differ.
(()=>{
  const profile=globalThis.WECHAT_NATIVE_PROFILE;
  const state={supported:false,installed:false,armed:false,accepted:0,failure:null};
  const hooks=[],timers=new Set(),windows=new Map(),callbacks=[];
  let active=null,base=null,disposed=false,dispatchSync,queue,complete,barrier;
  const snapshot=()=>({...state});
  function field(p){
    const short=p.add(23).readS8(),size=short<0?Number(p.add(8).readU64()):short;
    if(!Number.isSafeInteger(size)||size<0||size>256)throw Error('native_channels_login_field_changed');
    return (short<0?p.readPointer():p).readUtf8String(size);
  }
  function matches(w,request){
    return !disposed&&active===request&&!request.used&&Date.now()<request.expires&&
      w.readPointer().equals(base.add(0x9ba7e68))&&w.add(0x300).readU8()===0&&
      w.add(0x2f0).readU8()===0&&field(w.add(0x228))==='视频号创作平台'&&
      field(w.add(0x260))==='snsapi_login';
  }
  globalThis.WECHAT_CHANNELS_LOGIN={
    status:snapshot,
    arm(operationId,nonceDigest){
      if(!state.supported)return snapshot();
      if(typeof operationId!=='string'||!/^[a-f0-9-]{36}$/.test(operationId)||
         typeof nonceDigest!=='string'||!/^[a-f0-9]{64}$/.test(nonceDigest))throw Error('invalid_channels_login_request');
      if(active?.id===operationId&&active.digest===nonceDigest)return snapshot();
      active={id:operationId,digest:nonceDigest,expires:Date.now()+30000,used:false};
      state.armed=true;state.failure=null;
      return snapshot();
    },
    install(nativeBase){
      if(state.installed)return snapshot();
      if(Process.arch!=='arm64'||profile?.payloadSha256!==
         '5f5a81c4d8b000dec1c7203738fc4dba75ce752b55874fc1b6b5869f9dfb1027')return snapshot();
      try{
        base=nativeBase;
        const entries=[
          [0xf4a8a0,'ff8301d1f65703a9f44f04a9fd7b05a9fd430191f40302aaf50301aaf30300aa'],
          [0xf4ab48,'f44fbea9fd7b01a9fd430091f30300aae86204b00861399109410091090000f9'],
          [0xf4abdc,'fc6fbca9f65701a9f44f02a9fd7b03a9fdc30091ff030ad1f40301aaf30300aa'],
          [0xf4bcd0,'1f040071e0000054a0000035810000b4e00301aa01048052c9d08715c0035fd6']
        ];
        for(const [offset,expected] of entries){
          const address=base.add(offset),range=Process.findRangeByAddress(address);
          if(!range?.protection.includes('x'))throw Error('native_channels_login_not_executable');
          const actual=Array.from(new Uint8Array(address.readByteArray(32)),b=>b.toString(16).padStart(2,'0')).join('');
          if(actual!==expected)throw Error('native_channels_login_signature_mismatch');
        }
        dispatchSync=new NativeFunction(Module.getGlobalExportByName('dispatch_sync_f'),'void',
          ['pointer','pointer','pointer'],{scheduling:'cooperative'});
        queue=Module.getGlobalExportByName('_dispatch_main_q');
        // This is the actual native affirmative button callback. It builds the
        // response from this live window's AuthInfo and closes the window normally.
        complete=new NativeFunction(base.add(0xf4bcd0),'void',['int','pointer']);
        const confirm=new NativeCallback(context=>{
          const pending=windows.get(context.toString());
          if(!pending)return;
          windows.delete(context.toString());
          try{
            if(!matches(pending.window,pending.request))return;
            pending.request.used=true;state.armed=false;
            const capture=Memory.alloc(32);
            capture.add(0x10).writeU8(1);capture.add(0x18).writePointer(pending.window);
            complete(1,capture);state.accepted++;
          }catch(error){state.failure=error.message;state.armed=false;}
        },'void',['pointer']);
        barrier=new NativeCallback(()=>{},'void',['pointer']);callbacks.push(confirm,barrier);
        hooks.push(Interceptor.attach(base.add(0xf4a8a0),{
          onEnter(args){this.window=args[0];},
          onLeave(){
            const request=active;
            if(!request||disposed||request.used||Date.now()>=request.expires)return;
            try{
              const window=this.window;
              if(!matches(window,request))return;
              windows.set(window.toString(),{window,request});
              // The constructor's caller must finish before the native action.
              const timer=setTimeout(()=>{
                timers.delete(timer);
                if(!disposed&&windows.has(window.toString()))dispatchSync(queue,window,confirm);
              },0);
              timers.add(timer);
            }catch(error){state.failure=error.message;}
          }
        }));
        hooks.push(Interceptor.attach(base.add(0xf4ab48),{onEnter(args){windows.delete(args[0].toString());}}));
        state.installed=true;state.supported=true;
      }catch(error){state.failure=error.message;}
      return snapshot();
    },
    dispose(){
      disposed=true;active=null;state.armed=false;
      for(const timer of timers)clearTimeout(timer);
      timers.clear();windows.clear();
      if(dispatchSync&&barrier)dispatchSync(queue,ptr(0),barrier);
    }
  };
})();
