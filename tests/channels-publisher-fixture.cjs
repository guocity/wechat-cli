const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const optionsSource = fs.readFileSync(process.argv[3], 'utf8');
class Event { constructor(type) { this.type = type; } }
class Input { constructor() { this._value=''; this.events=[]; this.disabled=false; }
  get value() { return this._value; } set value(v) { this._value=v; }
  getClientRects() { return [1]; } getAttribute() { return null; }
  dispatchEvent(e) { this.events.push(e.type); }
}
const editor = new Input(); editor.innerText='';
const title = new Input();
const upload = new Input(); upload.accept='video/mp4,video/*'; upload.files=[];
const publish = new Input(); publish.textContent='发表'; publish.cssDisabled=true; publish.clicks=0;
publish.classList={contains:()=>publish.cssDisabled}; publish.click=()=>{publish.clicks++;};
const queries={'.input-editor[contenteditable]':[editor],
  'input[placeholder="填写短标题有机会获得更多流量"]':[title], 'input[type=file]':[upload],
  'button':[publish], '[role=dialog],.weui-desktop-dialog__wrp':[], 'video':[]};
const sandbox={location:{origin:'https://channels.weixin.qq.com',pathname:'/micro/content/post/create'},
  window:{},document:{querySelectorAll:s=>queries[s]||[]},getComputedStyle:()=>({visibility:'visible'}),
  HTMLInputElement:Input,Uint8Array,Event,InputEvent:Event,atob:s=>Buffer.from(s,'base64').toString('binary'),
  File:class {constructor(chunks,name){this.size=chunks.reduce((n,b)=>n+b.length,0);this.name=name;}},
  DataTransfer:class {constructor(){this.files=[];this.items={add:f=>this.files.push(f)};}}};
const context=vm.createContext(sandbox);
const call=command=>JSON.parse(vm.runInContext('(()=>{'+optionsSource+';const CHANNELS_COMMAND='+JSON.stringify(command)+';return (\n'+source+'\n)})()',context));
const id='11111111-1111-1111-1111-111111111111';
assert.equal(call({action:'status'}).status,'empty');
assert.equal(call({action:'begin',sessionId:id}).sessionId,id);
assert.equal(call({action:'begin',sessionId:'other'}).error,'channels_existing_draft');
assert.equal(call({action:'chunk',sessionId:id,offset:5,data:'YWJj'}).error,'channels_upload_sequence');
call({action:'chunk',sessionId:id,offset:0,data:'YWJj'});
assert.equal(call({action:'attach',sessionId:id,size:4}).error,'channels_upload_sequence');
assert.equal(call({action:'attach',sessionId:id,size:3,description:'caption',title:'title'}).status,'processing');
assert.equal(upload.files[0].size,3); assert.equal(title.value,'title');
assert.deepEqual(upload.events,['change']);
assert.equal(call({action:'post',sessionId:id}).error,'channels_upload_not_ready');
assert.equal(publish.clicks,0); // CSS disabled must block, even when button.disabled is false.
publish.cssDisabled=false;
assert.equal(call({action:'status'}).status,'ready');
assert.equal(call({action:'post',sessionId:'other'}).error,'channels_session_not_found');
// Settings commands must preserve the upload and never submit it.
call({action:'options-text',sessionId:id,description:'new description',title:'new title'});
assert.equal(title.value,'new title');
const root=new Input(); root.querySelector=()=>null;
queries['.post-position-wrap']=[root];
const makeOption=(name,detail)=>{const e=new Input();e.textContent=name;e.querySelector=sel=>sel.startsWith('.name')?{textContent:name}:detail?{textContent:detail}:null;e.click=()=>{e.selected=true;};return e;};
const p1=makeOption('Park','A'),p2=makeOption('Park','B');
queries['.post-position-wrap .location-filter-wrap .option-item']=[p1,p2];
assert.equal(call({action:'options-select',sessionId:id,kind:'location',value:'Park'}).error,'channels_option_ambiguous');
call({action:'options-select',sessionId:id,kind:'location',value:'Park | B'});
assert.equal(p2.selected,true);assert.equal(p1.selected,undefined);
assert.equal(call({action:'options-list',sessionId:id,kind:'location'}).choices.length,2);
assert.equal(call({action:'options-select',sessionId:id,kind:'location',value:'Missing'}).error,'channels_option_not_found');
const original=new Input();original.checked=false;original.click=()=>{original.checked=!original.checked;};
queries['.declare-original-checkbox input[type=checkbox]']=[original];
call({action:'options-original',sessionId:id,value:true});
assert.equal(original.checked,true);assert.equal(publish.clicks,0);
queries['[role=dialog],.weui-desktop-dialog__wrp']=[root];
assert.equal(call({action:'status'}).status,'action_required');
assert.equal(call({action:'post',sessionId:id}).error,'channels_dialog_open');
queries['[role=dialog],.weui-desktop-dialog__wrp']=[];
assert.equal(call({action:'post',sessionId:id}).status,'submission_requested');
assert.equal(call({action:'post',sessionId:id}).error,'channels_submission_already_requested');
assert.equal(publish.clicks,1);
sandbox.location.origin='https://example.com';
assert.equal(call({action:'status'}).error,'channels_page_not_found');
console.log('Channels publisher form-state fixture passed');
