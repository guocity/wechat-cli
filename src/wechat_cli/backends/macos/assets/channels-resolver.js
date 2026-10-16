// Runs only on the existing Finder worker. Never opens or plays a video.
(function(request) {
  'use strict';
  const key='__wechatCliNativeMedia';
  const role=typeof name==='string' && name==='finder' && location.pathname==='/finder/dist/xworker.js';
  if(request.operation==='identify')return JSON.stringify({finder:role});
  if(!role || typeof xwebCore?.invoke!=='function' || typeof handleApiCallback!=='function')return JSON.stringify({error:'native_finder_worker_unavailable'});
  const previous=globalThis[key];
  if(request.operation==='poll') {
    if(!previous || previous.id!==request.id)return JSON.stringify({error:'native_media_request_missing'});
    return JSON.stringify(previous.pending?{pending:true}:previous.result);
  }
  if(request.operation==='cancel') {
    if(previous?.id===request.id) {
      if(globalThis.handleApiCallback===previous.handler)globalThis.handleApiCallback=previous.original;
      delete globalThis[key];
    }
    return JSON.stringify({cancelled:true});
  }
  if(request.operation!=='start')return JSON.stringify({error:'invalid_native_media_operation'});
  if(previous?.pending)return JSON.stringify({error:'native_media_busy'});
  const state={id:request.id,pending:true,original:globalThis.handleApiCallback};
  state.handler=function(event) {
    if(event?.__callback_id!==request.id)return state.original(event);
    try {
      const response=event.__params;
      if(response?.jsapi_resp?.ret!==0)throw Error('native_media_resolve_failed');
      const body=JSON.parse(response.jsapi_resp.resp_json);
      const object=body.object;
      if(typeof object?.id!=='string' || object.id!==request.objectId)throw Error('native_media_identity_mismatch');
      const media=object.object_desc?.media?.find(item=>item.media_type===4);
      if(!media || typeof media.decode_key!=='string' || !/^\d{1,20}$/.test(media.decode_key))throw Error('native_media_descriptor_missing');
      const formats=(media.spec||[]).filter(s=>s.coding_format==='h264' && /^[A-Za-z0-9_-]{1,32}$/.test(s.file_format));
      formats.sort((a,b)=>(a.bit_rate||Infinity)-(b.bit_rate||Infinity));
      state.result={objectId:object.id,url:media.url,urlToken:media.url_token,decodeKey:media.decode_key,
        format:formats[0]?.file_format||null,fileName:media.file_name||null};
    } catch(error) {
      const allowed=['native_media_resolve_failed','native_media_identity_mismatch','native_media_descriptor_missing'];
      state.result={error:allowed.includes(error.message)?error.message:'native_media_resolve_failed'};
    } finally {
      state.pending=false;
      if(globalThis.handleApiCallback===state.handler)globalThis.handleApiCallback=state.original;
    }
    return JSON.stringify(null);
  };
  globalThis[key]=state;globalThis.handleApiCallback=state.handler;
  const parameters={url:'/cgi-bin/micromsg-bin/pc_findergetcommentdetail',cgi_cmdid:5259,scope:'finderLive',is_security_check:false,
    req_json:JSON.stringify({objectid:request.objectId,object_nonce_id:request.nonce,need_object:1,last_buffer:'',scene:146,direction:2,identity_scene:2,pull_scene:6})};
  // Exact envelope used by the shipped Finder worker's oldInvoke implementation.
  xwebCore.invoke('finderH5ExtTransfer',JSON.stringify({url:'https://channels.weixin.qq.com/web/pages/worker',api_tpye:'setresult',api:'SCENE_FETCHQUEUE',
    msg:JSON.stringify({__msg_type:'call',__callback_id:request.id,func:'finderH5ExtTransfer',params:parameters})}),request.id);
  return JSON.stringify({started:true});
})(__REQUEST__)
