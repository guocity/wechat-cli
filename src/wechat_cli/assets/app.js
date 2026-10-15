'use strict';
const el = id => document.getElementById(id);
let selected = '', chats = [], busy = false, sequence = 0;
const attempts = new Map(), urls = new Set();
const authenticated = fetch('/api/browser/session', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(async r=>{if(!r.ok)throw Error((await r.json()).message);});
async function api(path, data, binary=false) {
  await authenticated;
  const options={headers:{}};
  if(data!==undefined){
    const signature=path+JSON.stringify(data);
    if(!attempts.has(signature))attempts.set(signature,crypto.randomUUID());
    options.method='POST';options.headers={'Content-Type':'application/json','Idempotency-Key':attempts.get(signature)};options.body=JSON.stringify(data);
  }
  const response=await fetch(path,options);
  if(!response.ok){const error=await response.json();throw Error(error.message||error.error);}
  const result=binary?await response.blob():await response.json();
  if(data!==undefined)attempts.delete(path+JSON.stringify(data));
  return result;
}
function sync(){el('text').disabled=!selected||busy;el('send').disabled=!selected||busy||!el('text').value.trim();el('send-file').disabled=!selected||busy||!el('file').files.length;el('history').disabled=!selected||busy;}
async function action(task){try{await task();}catch(error){el('status').textContent=error.message;}}
function renderChats(){
  el('chats').replaceChildren();const q=el('search').value.toLocaleLowerCase();
  for(const chat of chats){
    if(!((chat.displayName||chat.displayString||'')+' '+chat.id).toLocaleLowerCase().includes(q))continue;
    const button=document.createElement('button'),name=document.createElement('strong'),meta=document.createElement('small');
    button.type='button';button.disabled=busy||chat.chatType==='folder';button.setAttribute('aria-current',String(chat.id===selected));
    name.textContent=(chat.isPinned?'📌 ':'')+(chat.displayName||chat.displayString||chat.id);
    meta.textContent=chat.lastMessageTime?new Date(chat.lastMessageTime*1000).toLocaleString():chat.id;
    button.append(name,meta);button.onclick=()=>action(async()=>{selected=chat.id;el('title').textContent=name.textContent;renderChats();sync();await history();});el('chats').append(button);
  }
}
async function refresh(){const result=await api('/api/native/chats');chats=result.items;renderChats();el('status').textContent=`${chats.length} conversations. Pinned first, then recent.`;}
function button(article,label,task){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>action(async()=>{b.disabled=true;try{await task();}finally{b.disabled=false;}});article.append(b);}
async function history(){
  const chatId=selected,request=++sequence;if(!chatId)return;
  const result=await api('/api/native/messages?'+new URLSearchParams({chatId,limit:el('limit').value}));
  if(request!==sequence||chatId!==selected)return;
  for(const url of urls)URL.revokeObjectURL(url);urls.clear();el('messages').replaceChildren();
  for(const message of result.items){
    const article=document.createElement('article'),meta=document.createElement('small'),text=document.createElement('p');
    meta.textContent=(message.senderName||message.groupSender||message.from||'Unknown sender')+' · '+(message.timestamp?new Date(message.timestamp*1000).toLocaleString():'')+' · '+message.typeLabel;
    text.textContent=message.displaySummary||message.summary;article.append(meta,text);
    const params={chatId,id:message.id};
    if(['image','sticker','video','voice','file'].includes(message.kind))button(article,message.kind==='file'?'Download file':'Load media',async()=>{
      const blob=await api('/api/native/media?'+new URLSearchParams(params),undefined,true);
      if(request!==sequence)return;
      const url=URL.createObjectURL(blob);urls.add(url);
      if(blob.type.startsWith('image/')){const img=document.createElement('img');img.src=url;img.alt=message.typeLabel;article.append(img);}
      else if(blob.type.startsWith('audio/')||blob.type.startsWith('video/')){const player=document.createElement(blob.type.startsWith('audio/')?'audio':'video');player.controls=true;player.src=url;article.append(player);}
      else{const link=document.createElement('a');link.href=url;link.download=message.details.fileName||'attachment';link.textContent='Save '+link.download;article.append(link);link.click();}
    });
    if(message.kind==='file')button(article,'Fetch in WeChat',async()=>{el('status').textContent=JSON.stringify(await api('/api/native/attachments/download',params));});
    if(message.kind==='image')button(article,'Extract text',async()=>{const r=await api('/api/native/images/text',params);const p=document.createElement('p');p.textContent=r.text;article.append(p);});
    if(message.kind==='voice'){
      button(article,'Transcribe',async()=>{const r=await api('/api/native/voice/transcribe',params);el('status').textContent=r.text||r.status;});
      button(article,'Read transcript',async()=>{const r=await api('/api/native/voice/transcript?'+new URLSearchParams(params));el('status').textContent=r.text||r.status;});
    }
    el('messages').append(article);
  }
  el('status').textContent=`${result.items.length} messages. Newest first.`;
}
el('compose').onsubmit=event=>{event.preventDefault();action(async()=>{
  if(busy||!selected||!el('text').value.trim())return;
  busy=true;sync();renderChats();
  try{const r=await api('/api/messages/send',{chatId:selected,text:el('text').value});if(!r.acceptedByClient)throw Error('Check WeChat before retrying.');el('text').value='';await history();el('status').textContent='Queued in WeChat.';}
  finally{busy=false;sync();renderChats();}
});};
el('upload').onsubmit=event=>{event.preventDefault();action(async()=>{
  const file=el('file').files[0];if(busy||!selected||!file)return;if(file.size>8*1024*1024)throw Error('Uploads must be at most 8 MiB.');
  busy=true;sync();renderChats();
  try{const bytes=new Uint8Array(await file.arrayBuffer());let encoded='';for(let i=0;i<bytes.length;i+=16384)encoded+=String.fromCharCode(...bytes.subarray(i,i+16384));
    const kind=el('file-kind').value;const data={chatId:selected,dataBase64:btoa(encoded),...(kind==='file'?{name:file.name}:{format:kind})};
    await api('/api/messages/'+(kind==='file'?'file':'voice'),data);el('file').value='';await history();el('status').textContent='Upload queued in WeChat.';
  }finally{busy=false;sync();renderChats();}
});};
el('refresh').onclick=()=>action(refresh);el('history').onclick=()=>action(history);el('connection').onclick=()=>action(async()=>{el('status').textContent=JSON.stringify(await api('/api/native/status'),null,2);});
el('search').oninput=renderChats;el('text').oninput=sync;el('file').onchange=sync;el('limit').onchange=()=>action(history);
action(refresh);
