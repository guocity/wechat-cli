const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../src/wechat_cli/backends/macos/assets/channels-options.js'),'utf8');
function fixture({body='原创权益 我已阅读并同意《原创声明须知》和《使用条款》',requested=true,acceptTerms=true,nativeController=false}={}){
 let open=true,accepted=0;
 const terms={checked:false,click(){this.checked=true;}};
 const confirm={disabled:false,textContent:'声明原创',getAttribute:()=>null,classList:{contains:()=>false},click(){accepted++;open=false;}};
 const dialog={innerText:body,querySelectorAll:s=>s==='input[type=checkbox]'?[terms]:s==='button'?[confirm]:[]};
 const controller={checkProto:false,visible:true,confirmDeclare(){accepted++;this.visible=false;open=false;}};
 if(nativeController){
  dialog.__vue__={$parent:controller};
  dialog.contains=e=>e===dialog;
  const fallback=dialog.querySelectorAll;
  dialog.querySelectorAll=s=>s==='.protocol-text'?[{click(){controller.checkProto=true;terms.checked=true;}}]:fallback(s);
 }
 const original={checked:true,click(){this.checked=!this.checked;}};
 const state={id:'session',consumed:false,originalityRequested:requested};
 const context={document:{querySelectorAll:s=>s==='[role=dialog],.weui-desktop-dialog__wrp'?(open?[dialog]:[]):s==='.declare-original-checkbox input[type=checkbox]'?[original]:s==='.declare-original-dialog'&&nativeController?[dialog]:[]}};
 vm.runInNewContext(source,context);
 const run=action=>context.channelsOptions({action,sessionId:'session',value:true,acceptTerms},{state,visible:()=>true,unique:list=>{assert.equal(list.length,1);return list[0];},snapshot:()=>({blockedByDialog:open,settings:{declareOriginal:original.checked}})});
 return {run,terms,confirm,state,accepted:()=>accepted};
}
test('explicit original-content request completes only the known declaration',()=>{const f=fixture();f.run('options-original-confirm');assert.equal(f.terms.checked,true);assert.equal(f.accepted(),0);f.run('options-original-confirm');assert.equal(f.accepted(),1);assert.equal(f.state.originalityConfirmed,true);});
test('without an explicit original-content request no agreement is accepted',()=>{const f=fixture({requested:false});assert.throws(()=>f.run('options-original-confirm'),/channels_originality_not_requested/);assert.equal(f.terms.checked,false);assert.equal(f.accepted(),0);});
test('an unrelated terms dialog is never accepted',()=>{const f=fixture({body:'Other terms of service'});assert.throws(()=>f.run('options-original-confirm'),/channels_dialog_open/);assert.equal(f.terms.checked,false);assert.equal(f.accepted(),0);});
test('a disabled originality confirmation must wait',()=>{const f=fixture();f.terms.checked=true;f.confirm.disabled=true;assert.throws(()=>f.run('options-original-confirm'),/channels_originality_not_ready/);assert.equal(f.accepted(),0);});

test('declaring original alone never accepts the platform terms',()=>{const f=fixture({acceptTerms:false});assert.throws(()=>f.run('options-original-confirm'),/channels_originality_not_requested/);assert.equal(f.terms.checked,false);assert.equal(f.accepted(),0);});

test('native declaration handler saves the approved model and closes the dialog',()=>{const f=fixture({nativeController:true});f.run('options-original-confirm');assert.equal(f.terms.checked,true);assert.equal(f.accepted(),0);f.run('options-original-confirm');assert.equal(f.accepted(),1);assert.equal(f.state.originalityConfirmed,true);});
