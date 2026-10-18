// Authenticated Channels webpage automation. No cookies, media tickets or raw HTTP API are exposed.
(()=>{
 try {
  if(location.origin!=='https://channels.weixin.qq.com'||!['/platform/post/list','/micro/content/post/list'].includes(location.pathname))
   throw Error('channels_page_not_found');
  const view=[...document.querySelectorAll('.post-list')].find(e=>e.__vue__?.postStore)?.__vue__;
  const store=view?.postStore;
  if(!view?.isPageReady||!Array.isArray(store?.list)||typeof store.changePageAsync!=='function')throw Error('channels_form_changed');
  const command=CHANNELS_COMMAND,source='wechat_embedded_browser';
  if(command.action==='status')return JSON.stringify({source,status:'ready'});
  const state=window.__wechatCliChannelsManager||(window.__wechatCliChannelsManager={jobs:new Map(),next:0,deletions:new Set()});
  const result=job=>job.error?{error:job.error}:job.result||{status:'pending',operationId:job.id};
  if(command.action==='job'){
   const job=state.jobs.get(command.operationId);
   if(!job)throw Error('channels_session_not_found');
   return JSON.stringify(result(job));
  }
  if(!['list','stats','delete'].includes(command.action))throw Error('channels_invalid_action');
  const count=value=>Number.isSafeInteger(value)&&value>=0?value:typeof value==='string'&&/^\d+$/.test(value)?(Number.isSafeInteger(Number(value))?Number(value):value):null;
  const metrics=row=>({views:count(row.readCount),likes:count(row.likeCount),comments:count(row.commentCount),
   shares:count(row.forwardAggregationCount),favorites:count(row.favCount)});
  const rowId=row=>typeof row?.objectId==='string'&&row.objectId.length>0?row.objectId:null;
  const video=row=>{
   const id=rowId(row);if(!id)throw Error('channels_video_id_unavailable');
   const media=row.desc?.media?.[0];
   return {videoId:id,title:Array.isArray(row.desc?.shortTitle)?row.desc.shortTitle.map(t=>t.shortTitle||'').join(''):null,
    description:typeof row.desc?.description==='string'?row.desc.description:'',createdAt:count(row.createTime),
    status:row.status??null,visibility:row.visibleType??null,declaredOriginal:row.originalInfo?.isDeclared==null?null:Boolean(row.originalInfo.isDeclared),
    originalityBadgeVisible:row.showOriginal??null,originalityAuditStatus:row.originalInfo?.auditOriginalFlag??null,stats:metrics(row),
    media:{durationSeconds:count(media?.videoPlayLen),width:count(media?.width),height:count(media?.height),fileSize:count(media?.fileSize)}};
  };
  const pageSize=Number(store.pageSize||view.PageSize);
  if(!Number.isSafeInteger(pageSize)||pageSize<1||pageSize>100)throw Error('channels_form_changed');
  const load=async page=>{
   const prior=store.list;
   await store.changePageAsync(page);
   if(!Array.isArray(store.list)||Number(store.curPage)!==page||store.list===prior)throw Error('channels_list_refresh_failed');
   if(!Number.isSafeInteger(Number(store.total))||Number(store.total)<0)throw Error('channels_form_changed');
   return store.list;
  };
  const total=()=>Number(store.total);
  const cursor=offset=>btoa(JSON.stringify({v:1,offset}));
  let offset=0;
  if(command.action==='list'){
   if(!Number.isInteger(command.limit)||command.limit<1||command.limit>100)throw Error('invalid_channels_limit');
   if(command.cursor!=null){
    try{const c=JSON.parse(atob(command.cursor));if(c.v!==1||!Number.isSafeInteger(c.offset)||c.offset<0||Object.keys(c).some(k=>!['v','offset'].includes(k)))throw Error();offset=c.offset;}
    catch(_){throw Error('invalid_channels_cursor');}
   }
  }
  if(command.action==='delete'&&(typeof command.videoId!=='string'||!command.videoId||command.videoId.length>1024||/[\x00-\x1f\x7f]/.test(command.videoId)))throw Error('invalid_channel_video_id');
  if(command.action==='delete'&&state.deletions.has(command.videoId))throw Error('channels_deletion_already_requested');
  if([...state.jobs.values()].some(j=>!j.result&&!j.error))throw Error('native_control_busy');
  // Keep pending native promises alive across CLI evaluations, with bounded results.
  for(const [key,job] of state.jobs)if(state.jobs.size>=32&&(job.result||job.error))state.jobs.delete(key);
  const job={id:String(++state.next),result:null,error:null};state.jobs.set(job.id,job);
  (async()=>{
   if(command.action==='list'){
    const items=[];let at=offset;
    await load(Math.floor(at/pageSize)+1);
    while(at<total()&&items.length<command.limit){
     const page=Math.floor(at/pageSize)+1;
     if(Number(store.curPage)!==page)await load(page);
     const index=at%pageSize,row=store.list[index];
     if(!row)throw Error('channels_list_changed');
     items.push(video(row));at++;
    }
    return {source,videos:items,totalVideos:total(),offset,nextCursor:at<total()?cursor(at):null};
   }
   if(command.action==='stats'){
    await load(1);
    const rows=store.list.map(metrics),totals={};
    for(const key of ['views','likes','comments','shares','favorites']){
     if(rows.some(r=>r[key]===null)){totals[key]=null;continue;}
     const sum=rows.reduce((n,r)=>n+BigInt(r[key]),0n);totals[key]=sum<=BigInt(Number.MAX_SAFE_INTEGER)?Number(sum):sum.toString();
    }
    return {source,totalVideos:total(),scope:'loaded_page',page:1,pageSize,loadedVideos:rows.length,stats:totals};
   }
   // The user supplies an exact ID. Resolve it from this account's fresh list
   // before invoking the same store action used by the webpage's confirm button.
   await load(1);let found=null;
   for(let page=1;page<=Math.max(1,Math.ceil(total()/pageSize));page++){
    if(page>500)throw Error('channels_video_search_limit');
    if(Number(store.curPage)!==page)await load(page);
    const matches=store.list.filter(r=>rowId(r)===command.videoId);
    if(matches.length>1)throw Error('channels_video_ambiguous');
    if(matches.length){found=matches[0];break;}
   }
   if(!found)throw Error('channels_video_not_found');
   if(typeof store.deletePost!=='function')throw Error('channels_form_changed');
   state.deletions.add(command.videoId); // Never repeat an uncertain native request.
   let response;
   try{response=await store.deletePost(command.videoId);}
   catch(_){throw Error('channels_deletion_outcome_unknown');}
   const code=response?.errCode,base=response?.data?.baseResp?.errcode;
   if(code!==0||base!=null&&base!==0){
    if(typeof code==='number'&&code!==0||typeof base==='number'&&base!==0){state.deletions.delete(command.videoId);throw Error('channels_delete_rejected');}
    throw Error('channels_deletion_outcome_unknown');
   }
   let verifiedAbsent=false;
   try{await load(Number(store.curPage));verifiedAbsent=!store.list.some(r=>rowId(r)===command.videoId);}catch(_){}
   return {source,videoId:command.videoId,status:'deleted',deleted:true,acknowledgedByServer:true,verifiedAbsent};
  })().then(value=>{job.result=value;}).catch(error=>{job.error=/^(channels_|invalid_|native_control_busy)/.test(String(error.message))?error.message:'channels_management_failed';});
  return JSON.stringify(result(job));
 }catch(error){return JSON.stringify({error:String(error.message||error)});}
})()
