// Official Channels OAuth through WeChat's own TLS loopback login service.
// The authorization nonce stays here; its one-way digest identifies this login operation.
(()=>{
  const p=new URL(location.href).searchParams;
  let redirect;
  try{redirect=new URL(p.get('redirect_uri'));}catch(_){return JSON.stringify({match:false});}
  if(location.origin!=='https://open.weixin.qq.com'||location.pathname!=='/connect/qrconnect'||
     p.get('appid')!=='wx20d84d087938fd46'||p.get('scope')!=='snsapi_login'||
     redirect.origin!=='https://channels.weixin.qq.com'||redirect.pathname!=='/platform/oauth-callback.html')
    return JSON.stringify({match:false});
  const prior=window.__wechatCliChannelsLogin;
  if(prior?.version===2&&!(prior.status==='retry_wait'&&Date.now()>=prior.nextCheckAt)){
    if(typeof CHANNELS_LOGIN_AUTHORIZE!=='undefined'&&CHANNELS_LOGIN_AUTHORIZE===true&&prior.status==='confirmation_ready')
      window.__wechatCliChannelsAuthorize();
    return JSON.stringify({match:true,...prior});
  }
  const state=window.__wechatCliChannelsLogin={version:2,status:'authenticating'};
  const jsdata={appid:p.get('appid'),scope:p.get('scope'),redirect_uri:p.get('redirect_uri'),state:p.get('state')||''};
  const ports=[14013,14014,14015,13013,13014,13015];
  const parse=value=>typeof value==='string'?JSON.parse(value):value;
  const request=(port,path,data)=>new Promise((resolve,reject)=>jQuery.ajax({
    url:'https://localhost.weixin.qq.com:'+port+path,type:'POST',cache:false,contentType:'application/json',
    timeout:path==='/api/authorize'?60000:8000,data:JSON.stringify(data),success:resolve,
    error:(xhr,reason)=>reject({httpStatus:xhr.status,reason})
  }));
  const failed=error=>{
    state.status='login_required';state.error=error instanceof Error?error.message:'native_authorization_failed';
    if(error?.httpStatus!==undefined)state.httpStatus=error.httpStatus;
    if(error?.reason)state.reason=error.reason;
  };
  (async()=>{
    let selected,check;
    for(const port of ports){
      try{
        const candidate=parse(await request(port,'/api/check-login',{apiname:'qrconnectchecklogin',jsdata}));
        if(candidate.errcode===0&&candidate.jsdata?.authorize_uuid){selected=port;check=candidate;break;}
      }catch(_){}
    }
    if(!selected){state.status='retry_wait';state.nextCheckAt=Date.now()+1500;return;}
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(check.jsdata.authorize_uuid));
    state.nonceDigest=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
    window.__wechatCliChannelsAuthorize=()=>{
      if(state.status!=='confirmation_ready')return;
      state.status='authorizing';
      (async()=>{
        const auth=parse(await request(selected,'/api/authorize',{apiname:'qrconnectfastauthorize',
          jsdata:{...jsdata,data:JSON.stringify({x:Math.round(screen.width/2-180),y:Math.round(screen.height/2-132)}),authorize_uuid:check.jsdata.authorize_uuid}}));
        if(auth.errcode!==0){state.status='login_required';state.code=auth.errcode;return;}
        const target=new URL(auth.jsdata.redirect_url);
        if(target.origin!=='https://channels.weixin.qq.com'||target.pathname!=='/platform/oauth-callback.html')
          throw Error('unexpected_channels_callback');
        state.status='authenticated';delete state.nonceDigest;
        location.assign(target.href);
      })().catch(failed);
    };
    state.status='confirmation_ready';
  })().catch(failed);
  return JSON.stringify({match:true,...state});
})()
