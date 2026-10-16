'use strict';
const el = id => document.getElementById(id);
let selected = '', chats = [], busy = false, sequence = 0, offline = false;
const attempts = new Map(), urls = new Set(), coverObservers = new Set();
let capabilities={};
const authenticated = fetch('/api/browser/session', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(async r=>{if(!r.ok)throw Error((await r.json()).message);});
async function api(path, data, binary=false) {
  if(offline)throw Error('WeChat session closed. Start wechat-cli again and reload this page.');
  await authenticated;
  const options={headers:{}};
  if(data!==undefined){
    const signature=path+JSON.stringify(data);
    if(!attempts.has(signature))attempts.set(signature,crypto.randomUUID());
    options.method='POST';options.headers={'Content-Type':'application/json','Idempotency-Key':attempts.get(signature)};options.body=JSON.stringify(data);
  }
  let response;
  try{response=await fetch(path,options);}catch(error){disconnected();throw error;}
  if(!response.ok){const error=await response.json();if(data!==undefined&&(path==='/api/moments/compose'||path==='/api/moments/action'&&(data.action!=='post'||error.error==='moment_finish_native_selection')))attempts.delete(path+JSON.stringify(data));throw Error(error.message||error.error);}
  let result=binary?await response.blob():await response.json();
  if(binary==='media'){
    const match=/filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition')||'');
    let filename='channel-video.mp4';
    if(match){try{filename=decodeURIComponent(match[1]);}catch(_){}}
    result={blob:result,filename};
  }
  if(data!==undefined)attempts.delete(path+JSON.stringify(data));
  return result;
}
function sync(){el('text').disabled=offline||!capabilities.sendMessages||!selected||busy;el('send').disabled=offline||!capabilities.sendMessages||!selected||busy||!el('text').value.trim();el('send-file').disabled=offline||!selected||busy||!el('file').files.length||!(el('file-kind').value==='file'?capabilities.fileSending:capabilities.voiceSending);el('history').disabled=offline||!selected||busy;syncMoment();}
function disconnected(){
  if(offline)return;offline=true;sequence++;
  for(const observer of coverObservers)observer.disconnect();coverObservers.clear();
  document.querySelectorAll('button,input,textarea,select').forEach(control=>{control.disabled=true;});
  el('status').textContent='WeChat session closed. Start wechat-cli again and reload this page.';
}
async function monitor(){
  if(offline)return;
  try{
    await authenticated;
    const response=await fetch('/api/health',{signal:AbortSignal.timeout(3000)});
    if(!response.ok||(await response.json()).status!=='ok')return disconnected();
  }catch(_){return disconnected();}
  setTimeout(monitor,2000);
}
async function action(task){try{await task();}catch(error){if(!offline)el('status').textContent=error.message;}}
function renderChats(){
  el('chats').replaceChildren();const q=el('search').value.toLocaleLowerCase();
  for(const chat of chats){
    if(!((chat.displayName||chat.displayString||'')+' '+chat.id).toLocaleLowerCase().includes(q))continue;
    const button=document.createElement('button'),name=document.createElement('strong'),meta=document.createElement('small');
    button.type='button';button.disabled=offline||busy||chat.chatType==='folder';button.setAttribute('aria-current',String(chat.id===selected));
    name.textContent=(chat.isPinned?'📌 ':'')+(chat.displayName||chat.displayString||chat.id);
    meta.textContent=chat.lastMessageTime?new Date(chat.lastMessageTime*1000).toLocaleString():chat.id;
    button.append(name,meta);button.onclick=()=>action(async()=>{selected=chat.id;el('title').textContent=name.textContent;renderChats();sync();await history();});el('chats').append(button);
  }
}
async function refresh(){const result=await api('/api/native/chats');chats=result.items;renderChats();el('status').textContent=`${chats.length} conversations. ${result.order==='pinned_then_recent'?'Pinned first, then recent.':'Most recent first.'}`;}
function button(article,label,task){const b=document.createElement('button');b.type='button';b.disabled=offline;b.textContent=label;b.onclick=()=>action(async()=>{b.disabled=true;try{await task();}finally{b.disabled=offline;}});article.append(b);}
function channelMedia(article,params,current){
  const status=document.createElement('p');status.setAttribute('role','status');
  const cover=document.createElement('img');cover.alt='Channels cover';cover.hidden=true;article.append(cover);
  cover.onerror=()=>{cover.hidden=true;coverStatus.textContent='Cover could not be displayed.';coverPending=undefined;retryCover.hidden=offline;};
  const coverStatus=document.createElement('small');coverStatus.textContent='Loading cover…';article.append(coverStatus);
  let pending, media, player, coverPending;
  const retryCover=document.createElement('button');retryCover.type='button';retryCover.textContent='Retry cover';retryCover.hidden=true;
  async function loadCover(){
    if(coverPending)return coverPending;
    retryCover.hidden=true;coverStatus.textContent='Loading cover…';
    coverPending=(async()=>{
      try{
        const blob=await api('/api/native/media?'+new URLSearchParams({...params,variant:'preview'}),undefined,true);
        if(!current())return;
        if(!blob.type.startsWith('image/'))throw Error('WeChat did not return a cover image.');
        const url=URL.createObjectURL(blob);urls.add(url);cover.src=url;cover.hidden=Boolean(player&&player.readyState>=2);if(player)player.poster=url;coverStatus.textContent='';
      }catch(error){if(current()){coverStatus.textContent=error.message;retryCover.hidden=offline;}coverPending=undefined;}
    })();
    return coverPending;
  }
  retryCover.onclick=()=>loadCover();article.append(retryCover);
  const observer=new IntersectionObserver(entries=>{
    if(entries.some(entry=>entry.isIntersecting)){
      observer.disconnect();coverObservers.delete(observer);if(current())loadCover();
    }
  },{rootMargin:'200px'});
  coverObservers.add(observer);observer.observe(article);
  async function load(){
    if(!pending)pending=api('/api/native/media?'+new URLSearchParams(params),undefined,'media').catch(error=>{pending=undefined;throw error;});
    const result=await pending;
    if(!current())return null;
    if(!media){
      if(result.blob.type!=='video/mp4')throw Error('This Channels card did not return an MP4.');
      const url=URL.createObjectURL(result.blob);urls.add(url);media={...result,url};
    }
    return media;
  }
  async function attempt(task){status.textContent='Preparing video in WeChat and loading MP4 for this viewer…';try{await task();}catch(error){status.textContent=error.message;}}
  button(article,'Play video',()=>attempt(async()=>{
    const result=await load();if(!result)return;
    if(!player){player=document.createElement('video');player.controls=true;player.preload='metadata';player.poster=cover.src;player.src=result.url;
      player.onloadeddata=()=>{cover.hidden=true;};
      player.onerror=()=>{status.textContent='The browser cannot decode this video. Use Download MP4 to save it.';};article.append(player);}
    try{await player.play();status.textContent='Playing Channels video.';}catch(_){status.textContent='Video loaded. Use the player controls to play.';}
  }));
  button(article,'Download MP4',()=>attempt(async()=>{
    const result=await load();if(!result)return;
    const link=document.createElement('a');link.href=result.url;link.download=result.filename;link.textContent='Save '+result.filename;
    article.append(link);link.click();status.textContent='Download ready: '+result.filename;
  }));
  article.append(status);
}
async function history(){
  const chatId=selected,request=++sequence;if(!chatId)return;
  const result=await api('/api/native/messages?'+new URLSearchParams({chatId,limit:el('limit').value}));
  if(request!==sequence||chatId!==selected)return;
  for(const observer of coverObservers)observer.disconnect();coverObservers.clear();
  for(const url of urls)URL.revokeObjectURL(url);urls.clear();el('messages').replaceChildren();
  for(const message of result.items){
    const article=document.createElement('article'),meta=document.createElement('small'),text=document.createElement('p');
    meta.textContent=(message.senderName||message.groupSender||message.from||'Unknown sender')+' · '+(message.timestamp?new Date(message.timestamp*1000).toLocaleString():'')+' · '+message.typeLabel;
    text.textContent=message.displaySummary||message.summary;article.append(meta,text);
    const params={chatId,id:message.id};
    if(capabilities.mediaPreviews&&message.kind==='channels_video')channelMedia(article,params,()=>request===sequence&&!offline);
    if(capabilities.mediaPreviews&&['image','sticker','video','voice','file'].includes(message.kind))button(article,message.kind==='file'?'Download file':'Load media',async()=>{
      const blob=await api('/api/native/media?'+new URLSearchParams(params),undefined,true);
      if(request!==sequence)return;
      const url=URL.createObjectURL(blob);urls.add(url);
      if(blob.type.startsWith('image/')){const img=document.createElement('img');img.src=url;img.alt=message.typeLabel;article.append(img);}
      else if(blob.type.startsWith('audio/')||blob.type.startsWith('video/')){const player=document.createElement(blob.type.startsWith('audio/')?'audio':'video');player.controls=true;player.src=url;article.append(player);}
      else{const link=document.createElement('a');link.href=url;link.download=message.details.fileName||'attachment';link.textContent='Save '+link.download;article.append(link);link.click();}
    });
    if(capabilities.mediaPreviews&&message.kind==='file')button(article,'Fetch in WeChat',async()=>{el('status').textContent=JSON.stringify(await api('/api/native/attachments/download',params));});
    if(capabilities.imageTextExtraction&&message.kind==='image')button(article,'Extract text',async()=>{const r=await api('/api/native/images/text',params);const p=document.createElement('p');p.textContent=r.text;article.append(p);});
    if(capabilities.voiceToText&&message.kind==='voice'){
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
el('search').oninput=renderChats;el('text').oninput=sync;el('file').onchange=sync;el('file-kind').onchange=sync;el('limit').onchange=()=>action(history);
action(async()=>{capabilities=await api('/api/capabilities');el('upload').hidden=!(capabilities.fileSending||capabilities.voiceSending);await refresh();sync();});monitor();

// Prepare in the browser, then keep account-dependent editing in WeChat's full composer.
let momentFiles=[], momentSession='', momentUrls=[];
const momentDialog=el('moment-dialog');
function syncMoment(){
  el('open-moment').hidden=!capabilities.momentsPosting;
  el('open-moment').disabled=offline||busy;
  el('moment-prepare').disabled=offline||busy||!momentFiles.length;
  document.querySelectorAll('[data-moment-action]').forEach(b=>{b.disabled=offline||busy||!momentSession;});
  el('moment-files').disabled=offline||busy;
  el('moment-text').disabled=offline||busy;
}
function renderMomentMedia(){
  momentUrls.forEach(url=>URL.revokeObjectURL(url));momentUrls=[];
  el('moment-preview').replaceChildren();
  momentFiles.forEach((file,index)=>{
    const figure=document.createElement('figure'),preview=document.createElement(file.type.startsWith('video/')?'video':'img');
    const url=URL.createObjectURL(file);momentUrls.push(url);preview.src=url;
    if(preview.tagName==='VIDEO'){preview.controls=true;preview.preload='metadata';}else preview.alt=file.name;
    const caption=document.createElement('figcaption');caption.textContent=file.name;
    const remove=document.createElement('button');remove.type='button';remove.textContent='Remove';
    remove.onclick=()=>{if(busy)return;momentFiles.splice(index,1);renderMomentMedia();syncMoment();};
    figure.append(preview,caption,remove);el('moment-preview').append(figure);
  });
}
el('open-moment').onclick=()=>{syncMoment();momentDialog.showModal();};
el('moment-close').onclick=()=>momentDialog.close();
el('moment-files').onchange=()=>action(async()=>{
  const added=[...el('moment-files').files];el('moment-files').value='';
  const files=[...momentFiles,...added], videos=files.filter(f=>f.type.startsWith('video/'));
  if(files.length>9||videos.length&&(files.length!==1||videos.length!==1))throw Error('Choose up to 9 photos or one video; media types cannot be mixed.');
  if(files.some(f=>!['image/jpeg','image/png','video/mp4','video/quicktime'].includes(f.type)))throw Error('Choose JPEG/PNG photos or an MP4/MOV video.');
  if(files.some(f=>f.size>(f.type.startsWith('video/')?64:8)*1024**2)||files.reduce((n,f)=>n+f.size,0)>64*1024**2)throw Error('Photos: 8 MiB each. Video and combined total: 64 MiB.');
  momentFiles=files;renderMomentMedia();syncMoment();
});
async function momentTask(task){
  if(busy||offline)return;busy=true;sync();syncMoment();
  try{await task();}catch(error){el('moment-status').textContent=error.message;}
  finally{busy=false;sync();syncMoment();}
}
el('moment-form').onsubmit=event=>{event.preventDefault();momentTask(async()=>{
  if(!momentFiles.length)return;
  el('moment-status').textContent='Validating media and opening WeChat…';
  const media=[];
  for(const file of momentFiles){const bytes=new Uint8Array(await file.arrayBuffer());let encoded='';for(let i=0;i<bytes.length;i+=16384)encoded+=String.fromCharCode(...bytes.subarray(i,i+16384));media.push({dataBase64:btoa(encoded)});}
  const result=await api('/api/moments/compose',{text:el('moment-text').value,kind:momentFiles[0].type.startsWith('video/')?'video':'images',media});
  momentSession=result.sessionId;el('moment-form').hidden=true;el('moment-native').hidden=false;
  el('moment-status').textContent='Draft open in WeChat. Edit there, choose mentions and visibility, then Post or discard.';
});};
function resetMoment(){momentSession='';momentFiles=[];renderMomentMedia();el('moment-text').value='';el('moment-native').hidden=true;el('moment-form').hidden=false;}
document.querySelectorAll('[data-moment-action]').forEach(button=>button.onclick=()=>momentTask(async()=>{
  const action=button.dataset.momentAction;
  const result=await api('/api/moments/action',{sessionId:momentSession,action});
  if(['submitted','cancelled','closed'].includes(result.status)){
    resetMoment();
    el('moment-status').textContent=result.status==='submitted'?'Submitted through WeChat. Check Moments for publication; server acknowledgement is unavailable.':result.status==='closed'?'Native composer closed. Check Moments if you posted directly in WeChat.':'Draft discarded.';
  }else el('moment-status').textContent=result.status==='native_selection'?'Complete this selection in WeChat, then return here.':'Native composer is open. Caption: '+(result.text||'(empty)');
}));
