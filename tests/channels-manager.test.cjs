const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/wechat_cli/backends/macos/assets/channels-manager.js'),'utf8');
function fixture({response={errCode:0,data:{baseResp:{errcode:0}}},failDelete=false,refresh=false,origin='https://channels.weixin.qq.com'}={}){
 const rows=Array.from({length:5},(_,i)=>({objectId:'export/900719925474099'+i,desc:{description:'Video '+i,shortTitle:[{shortTitle:'Title '+i}],media:[{videoPlayLen:10,width:1280,height:720,fileSize:'12000000',url:'private-ticket'}]},createTime:1700000000+i,status:1,visibleType:0,readCount:i+10,likeCount:i,commentCount:0,forwardCount:1,forwardAggregationCount:3,favCount:2}));
 const calls=[];
 const store={list:rows.slice(0,2),total:rows.length,curPage:1,pageSize:2,
 async changePageAsync(page){calls.push(['page',page]);store.curPage=page;if(!refresh)store.list=rows.slice((page-1)*2,page*2);store.total=rows.length;},
 async deletePost(id){calls.push(['delete',id]);if(failDelete)throw Error('connection lost');if(response?.errCode===0&&!(response?.data?.baseResp?.errcode)){const i=rows.findIndex(r=>r.objectId===id);if(i>=0)rows.splice(i,1);}return response;}};
 const view={isPageReady:true,PageSize:2,postStore:store};
 const context={window:{},location:{origin,pathname:'/micro/content/post/list'},document:{querySelectorAll:s=>s==='.post-list'?[{__vue__:view}]:[]},btoa:s=>Buffer.from(s).toString('base64'),atob:s=>Buffer.from(s,'base64').toString()};
 const sandbox=vm.createContext(context);
 const run=command=>{sandbox.CHANNELS_COMMAND=command;return JSON.parse(vm.runInContext(source,sandbox));};
 const complete=async command=>{let r=run(command);for(let i=0;i<10&&r.status==='pending';i++){await new Promise(resolve=>setImmediate(resolve));r=run({action:'job',operationId:r.operationId});}return r;};
 return {run,complete,calls,rows,store,view};
}
test('foreign webpage never reads or deletes a video',async()=>{const f=fixture({origin:'https://example.com'});assert.equal((await f.complete({action:'delete',videoId:'x'})).error,'channels_page_not_found');assert.deepEqual(f.calls,[]);});
test('loading webpage is not treated as authenticated readiness',()=>{const f=fixture();f.view.isPageReady=false;assert.equal(f.run({action:'status'}).error,'channels_form_changed');});
test('listing spans native pages with exact IDs and an opaque continuation cursor',async()=>{const f=fixture();f.rows[0].showOriginal=false;f.rows[0].originalInfo={isDeclared:1,auditOriginalFlag:0};const a=await f.complete({action:'list',limit:3,cursor:null});assert.equal(a.videos[0].declaredOriginal,true);assert.equal(a.videos[0].originalityBadgeVisible,false);assert.equal(a.videos[0].originalityAuditStatus,0);assert.equal(a.totalVideos,5);assert.deepEqual(a.videos.map(v=>v.videoId),f.rows.slice(0,3).map(r=>r.objectId));assert.equal(a.videos[0].stats.shares,3);assert.equal(JSON.stringify(a).includes('private-ticket'),false);const b=await f.complete({action:'list',limit:3,cursor:a.nextCursor});assert.deepEqual(b.videos.map(v=>v.description),['Video 3','Video 4']);assert.equal(b.nextCursor,null);});
test('invalid cursors fail before page requests',async()=>{const f=fixture();assert.equal((await f.complete({action:'list',limit:2,cursor:'bad'})).error,'invalid_channels_cursor');assert.deepEqual(f.calls,[]);});
test('stats describe the loaded page and use exact displayed share counts',async()=>{const f=fixture();const r=await f.complete({action:'stats'});assert.equal(r.scope,'loaded_page');assert.equal(r.totalVideos,5);assert.equal(r.loadedVideos,2);assert.deepEqual(r.stats,{views:21,likes:1,comments:0,shares:6,favorites:4});});
test('unavailable counts remain unknown rather than zero',async()=>{const f=fixture();delete f.rows[0].readCount;const r=await f.complete({action:'stats'});assert.equal(r.stats.views,null);});
test('delete finds the exact video on a later page and verifies server acknowledgement',async()=>{const f=fixture();const id=f.rows[4].objectId;const r=await f.complete({action:'delete',videoId:id});assert.equal(r.deleted,true);assert.equal(r.acknowledgedByServer,true);assert.equal(r.verifiedAbsent,true);assert.deepEqual(f.calls.filter(c=>c[0]==='delete'),[['delete',id]]);assert.equal((await f.complete({action:'delete',videoId:id})).error,'channels_deletion_already_requested');});
test('unknown ID cannot delete a neighboring row',async()=>{const f=fixture();assert.equal((await f.complete({action:'delete',videoId:'export/missing'})).error,'channels_video_not_found');assert.equal(f.calls.some(c=>c[0]==='delete'),false);});
test('failed refresh does not mutate stale list data',async()=>{const f=fixture({refresh:true});assert.equal((await f.complete({action:'delete',videoId:f.rows[0].objectId})).error,'channels_list_refresh_failed');assert.equal(f.calls.some(c=>c[0]==='delete'),false);});
test('server rejection never becomes a successful deletion',async()=>{const f=fixture({response:{errCode:123}});assert.equal((await f.complete({action:'delete',videoId:f.rows[0].objectId})).error,'channels_delete_rejected');});
test('missing acknowledgement is uncertain and cannot be retried automatically',async()=>{const f=fixture({response:{}}),id=f.rows[0].objectId;assert.equal((await f.complete({action:'delete',videoId:id})).error,'channels_deletion_outcome_unknown');assert.equal((await f.complete({action:'delete',videoId:id})).error,'channels_deletion_already_requested');assert.equal(f.calls.filter(c=>c[0]==='delete').length,1);});
test('lost delete connection preserves uncertainty and blocks another native submission',async()=>{const f=fixture({failDelete:true}),id=f.rows[0].objectId;assert.equal((await f.complete({action:'delete',videoId:id})).error,'channels_deletion_outcome_unknown');assert.equal((await f.complete({action:'delete',videoId:id})).error,'channels_deletion_already_requested');});
