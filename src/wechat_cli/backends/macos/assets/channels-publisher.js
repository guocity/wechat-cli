// Runs only in the exact signed-in Channels create page. No cookies or tickets leave WeChat.
(function () {
  try {
    const paths = ['/platform/post/create', '/micro/content/post/create'];
    if (location.origin !== 'https://channels.weixin.qq.com' || !paths.includes(location.pathname))
      return JSON.stringify({error: 'channels_page_not_found'});
    const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
    const unique = list => { if (list.length !== 1) throw Error('channels_form_changed'); return list[0]; };
    const editor = unique([...document.querySelectorAll('.input-editor[contenteditable]')].filter(visible));
    const title = unique([...document.querySelectorAll('input[placeholder="填写短标题有机会获得更多流量"]')].filter(visible));
    const upload = unique([...document.querySelectorAll('input[type=file]')].filter(e => e.accept.includes('video')));
    const publish = unique([...document.querySelectorAll('button')].filter(e => visible(e) && e.textContent.trim() === '发表'));
    const disabled = e => e.disabled || e.getAttribute('aria-disabled') === 'true' || e.classList.contains('weui-desktop-btn_disabled');
    const dialogs = [...document.querySelectorAll('[role=dialog],.weui-desktop-dialog__wrp')].filter(visible);
    let state = window.__wechatCliChannels;
    const linkComponent = [...document.querySelectorAll('.post-with-link')].find(e=>e.__vue__?.$options.methods?.parseLink)?.__vue__;
    const postView=[...document.querySelectorAll('.post-create-wrap')].find(e=>e.__vue__?.postValidState)?.__vue__;
    const mediaView=[...document.querySelectorAll('.post-edit-wrap')].find(e=>Array.isArray(e.__vue__?.fileList))?.__vue__;
    // The button DOM can remain enabled while its upload is still starting.
    // Use the page's actual validation and server-upload completion as well.
    const ready=()=>!disabled(publish)&&(!postView||postView.postValidState.canPost===true)&&
      (!mediaView||(mediaView.fileList.length===1&&mediaView.fileList[0].status==='done'));
    const snapshot = () => ({source:'wechat_embedded_browser', path:location.pathname,
      sessionId:state?.id || null, status:state?.consumed ? 'submission_requested' : dialogs.length ? 'action_required' : state?.attached ? (!ready() ? 'processing' : 'ready') : state ? 'receiving' : 'empty',
      publishEnabled:ready(), blockedByDialog:dialogs.length > 0,
      receivedBytes:state?.bytes || 0,
      settings:{description:editor.innerText, title:title.value,
        location:document.querySelectorAll('.position-display')[0]?.innerText?.trim() || '',
        link:document.querySelectorAll('.link-display-wrap')[0]?.innerText?.trim() || '',
        linkUrl:document.querySelectorAll('.link-input-wrap input[type=text]')[0]?.value || '',
        activity:document.querySelectorAll('.activity-display')[0]?.innerText?.trim() || '',
        declareOriginal:document.querySelectorAll('.declare-original-checkbox input[type=checkbox]')[0]?.checked || false,
        scheduledAt:document.querySelectorAll('.post-time-wrap')[0]?.__vue__?.time || null,
        videoLabel:document.querySelectorAll('.mark-tag-select .select-display')[0]?.innerText?.trim() || ''},
      validation:{linkError:linkComponent?.linkError || '',linkParsing:linkComponent?.isParsing || false},
      acknowledgedByServer:false});
    const command = CHANNELS_COMMAND;
    if (command.action.startsWith('options-')) return JSON.stringify(channelsOptions(command,{visible,unique,state,editor,title,snapshot}));
    if (command.action === 'status') return JSON.stringify(snapshot());
    if (command.action === 'begin') {
      if (state || upload.files.length || editor.innerText.trim() || title.value || [...document.querySelectorAll('video')].some(visible))
        throw Error('channels_existing_draft');
      if (dialogs.length) throw Error('channels_dialog_open');
      state = window.__wechatCliChannels = {id:command.sessionId, chunks:[], bytes:0, attached:false, consumed:false};
      return JSON.stringify(snapshot());
    }
    if (!state || state.id !== command.sessionId) throw Error('channels_session_not_found');
    if (state.consumed) throw Error('channels_submission_already_requested');
    if (command.action === 'chunk') {
      if (state.attached || state.bytes !== command.offset) throw Error('channels_upload_sequence');
      const raw = atob(command.data), bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
      if (state.bytes + bytes.length > 256 * 1024 * 1024) throw Error('channels_upload_too_large');
      state.chunks.push(bytes); state.bytes += bytes.length;
    } else if (command.action === 'attach') {
      if (state.attached || state.bytes !== command.size) throw Error('channels_upload_sequence');
      if (dialogs.length) throw Error('channels_dialog_open');
      editor.textContent = command.description;
      editor.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:command.description}));
      // This shipped editor publishes its model over an event bus rather than
      // a DOM input listener. Use its normal change handler after setting text.
      const desc=[...document.querySelectorAll('.post-desc-box')].find(e=>e.__vue__?.$refs?.inputEditor===editor)?.__vue__;
      desc?.updateDescData?.();
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      setter.call(title, command.title); title.dispatchEvent(new Event('input',{bubbles:true}));
      title.dispatchEvent(new Event('change',{bubbles:true}));
      const file = new File(state.chunks, command.name || 'video.mp4', {type:'video/mp4'});
      const transfer = new DataTransfer(); transfer.items.add(file);
      upload.files = transfer.files;
      state.attached = true; state.chunks = [];
      upload.dispatchEvent(new Event('change', {bubbles:true}));
    } else if (command.action === 'post') {
      if (linkComponent?.linkError || linkComponent?.isParsing) throw Error('channels_link_not_ready');
      if (!state.attached || !ready()) throw Error('channels_upload_not_ready');
      if (dialogs.length) throw Error('channels_dialog_open');
      // Consume before clicking. Never repeat an uncertain submission, even with another API key.
      state.consumed = true;
      publish.click();
    } else throw Error('channels_invalid_action');
    return JSON.stringify(snapshot());
  } catch (error) {
    const message=String(error.message || error);
    // The portal deliberately aborts its parent bootstrap while replacing the iframe.
    const initializing=CHANNELS_COMMAND.action==='status'&&message==='此报错可以忽略，iframe主动中断主应用代码在子应用运行';
    return JSON.stringify({error:initializing?'channels_form_changed':message});
  }
})()
