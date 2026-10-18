const {test}=require('node:test');
const {createHash}=require('node:crypto');
const webcrypto={subtle:{digest:async(_,bytes)=>Uint8Array.from(createHash('sha256').update(bytes).digest()).buffer}};
const {TextEncoder}=require('node:util');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const source=readFileSync(path.join(__dirname,'../src/wechat_cli/backends/macos/assets/channels-login.js'),'utf8');
const callback='https://channels.weixin.qq.com/platform/oauth-callback.html';
function fixture({appid='wx20d84d087938fd46',target=callback+'?code=private',reject=false}={}) {
 const requests=[],redirects=[];
 const href='https://open.weixin.qq.com/connect/qrconnect?'+new URLSearchParams({appid,scope:'snsapi_login',redirect_uri:callback});
 const u=new URL(href),window={};
 const context={URL,JSON,Promise,Error,Math,crypto:webcrypto,TextEncoder,screen:{width:1200,height:900},window,location:{href,origin:u.origin,pathname:u.pathname,assign:value=>redirects.push(value)},
 jQuery:{ajax(options){const body=JSON.parse(options.data);requests.push(body.apiname);
 queueMicrotask(()=>{
 if(reject)return options.error({status:0});
 options.success(JSON.stringify(body.apiname==='qrconnectchecklogin'?{errcode:0,jsdata:{authorize_uuid:'private-uuid'}}:{errcode:0,jsdata:{redirect_url:target}}));
 });}}};
 return {context,requests,redirects,window,run:()=>JSON.parse(vm.runInNewContext(context.CHANNELS_LOGIN_AUTHORIZE?'(()=>{const CHANNELS_LOGIN_AUTHORIZE=true;return (\n'+source+'\n);})()':source,context))};
}
const drain=()=>new Promise(resolve=>setTimeout(resolve,30));
async function authorize(f){f.run();await drain();f.context.CHANNELS_LOGIN_AUTHORIZE=true;f.run();await drain();}
test('foreign OAuth app is rejected without requesting account authorization',()=>{
 const f=fixture({appid:'foreign'});assert.equal(f.run().match,false);assert.deepEqual(f.requests,[]);
});
test('official quick-login keeps native authorization data in the document and starts only once',async()=>{
 const f=fixture();f.run();f.run();await drain();
 assert.deepEqual(f.requests,['qrconnectchecklogin']);
 assert.equal(f.run().status,'confirmation_ready');
 assert.match(f.run().nonceDigest,/^[a-f0-9]{64}$/);
 f.context.CHANNELS_LOGIN_AUTHORIZE=true;f.run();f.run();await drain();
 assert.deepEqual(f.requests,['qrconnectchecklogin','qrconnectfastauthorize']);
 assert.equal(f.redirects.length,1);assert.equal(f.run().status,'authenticated');
 assert.equal(JSON.stringify(f.run()).includes('private'),false);
});
test('unexpected authentication redirect fails closed',async()=>{
 const f=fixture({target:'https://example.com/steal'});await authorize(f);
 assert.equal(f.run().status,'login_required');assert.deepEqual(f.redirects,[]);
});
test('unavailable native login service does not submit authorization',async()=>{
 const f=fixture({reject:true});f.run();await drain();
 assert.equal(f.run().status,'retry_wait');assert.equal(f.requests.includes('qrconnectfastauthorize'),false);
});
