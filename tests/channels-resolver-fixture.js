// Exercise the real worker script against a local synthetic native callback.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const template = fs.readFileSync(process.argv[2], 'utf8');
for (const mismatch of [false, true]) {
  let invoked, forwarded=0;
  const original = () => {forwarded++; return 'delegated';};
  const context = vm.createContext({name:'finder',location:{pathname:'/finder/dist/xworker.js'},
    handleApiCallback:original,xwebCore:{invoke(...args){invoked=args;}}});
  const evaluate = request => JSON.parse(vm.runInContext(template.replace('__REQUEST__',JSON.stringify(request)),context));
  assert.equal(evaluate({operation:'identify'}).finder,true);
  assert.equal(evaluate({operation:'start',id:'test-id',objectId:'123',nonce:'nonce'}).started,true);
  assert.equal(invoked[0],'finderH5ExtTransfer');
  const payload = JSON.parse(JSON.parse(invoked[1]).msg);
  const request = JSON.parse(payload.params.req_json);
  assert.equal(request.objectid,'123');assert.equal(request.object_nonce_id,'nonce');
  assert.equal(context.handleApiCallback({__callback_id:'unrelated'}),'delegated');
  assert.equal(forwarded,1);
  const media={media_type:4,url:'https://finder.video.qq.com/media',url_token:'&token=test',decode_key:'1',
    spec:[{coding_format:'hevc',file_format:'hevc',bit_rate:1},
      {coding_format:'h264',file_format:'large',bit_rate:1000},{coding_format:'h264',file_format:'small',bit_rate:100}]};
  context.handleApiCallback({__callback_id:'test-id',__params:{jsapi_resp:{ret:0,
    resp_json:JSON.stringify({object:{id:mismatch?'456':'123',object_desc:{media:[media]}}})}}});
  const result=evaluate({operation:'poll',id:'test-id'});
  if(mismatch)assert.equal(result.error,'native_media_identity_mismatch');
  else {assert.equal(result.objectId,'123');assert.equal(result.format,'small');assert.equal(result.decodeKey,'1');}
  assert.equal(context.handleApiCallback,original);
  evaluate({operation:'cancel',id:'test-id'});
  assert.equal(context.__wechatCliNativeMedia,undefined);
  evaluate({operation:'start',id:'pending',objectId:'123',nonce:'nonce'});
  evaluate({operation:'cancel',id:'pending'});
  assert.equal(context.handleApiCallback,original);
}
