// Page controls only. Originality confirmation requires an explicit original-content request; unrelated agreements are never accepted.
function channelsOptions(command, context) {
  const {visible, unique, state, editor, title, snapshot} = context;
  const all = selector => [...document.querySelectorAll(selector)];
  const one = selector => unique(all(selector).filter(visible));
  const text = e => e.textContent.trim().replace(/\s+/g,' ');
  const input = (element, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(element,value);
    element.dispatchEvent(new Event('input',{bubbles:true}));
    element.dispatchEvent(new Event('change',{bubbles:true}));
    element.dispatchEvent(new Event('blur',{bubbles:true}));
  };
  if (!state || state.id !== command.sessionId) throw Error('channels_session_not_found');
  if (state.consumed) throw Error('channels_submission_already_requested');
  const originalDialog = d => {
    const value=d.innerText||d.textContent||'';
    return ['原创权益','原创声明须知','使用条款','我已阅读并同意'].every(t=>value.includes(t));
  };
  const kind = command.kind;
  const roots = {location:'.post-position-wrap',activity:'.post-activity-wrap',link:'.post-link-wrap',videoLabel:'.mark-tag-select'};
  const displays = {location:'.position-display-wrap',activity:'.activity-display-wrap',link:'.link-display-wrap',videoLabel:'.select-display'};
  const items = {location:'.location-filter-wrap .option-item',activity:'.activity-filter-wrap .option-item',link:'.link-list-options .link-option-item',videoLabel:'.mark-tag-options .mark-tag-option'};
  const option = e => {
    const name=e.querySelector('.name,.option-main,.title-wrap span');
    const desc=e.querySelector('.desc,.option-sub');
    const label=name?text(name):text(e);
    return {name:label, detail:desc?text(desc):'', value:label+(desc?' | '+text(desc):'')};
  };
  if (command.action === 'options-list') {
    const root=one(roots[kind]);
    return {...snapshot(),choices:all(roots[kind]+' '+items[kind]).map(option),kind};
  }
  if (command.action === 'options-open') {
    const root=one(roots[kind]);
    // Open only if its list is currently hidden.
    if (!all(roots[kind]+' '+items[kind]).some(visible)) one(roots[kind]+' '+displays[kind]).click();
    if (command.query !== undefined) {
      const placeholders={location:'搜索附近位置',activity:'搜索活动'};
      input(one(roots[kind]+' input[placeholder="'+placeholders[kind]+'"]'),command.query);
    }
  } else if (command.action === 'options-select') {
    const candidates=all(roots[kind]+' '+items[kind]).filter(visible).filter(e=>{
      const value=option(e);return value.value===command.value || value.name===command.value;
    });
    if (!candidates.length) throw Error('channels_option_not_found');
    if (candidates.length!==1) throw Error('channels_option_ambiguous');
    candidates[0].click();
  } else if (command.action === 'options-text') {
    if ('description' in command) {
      editor.textContent=command.description;
      editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:command.description}));
      const desc=[...document.querySelectorAll('.post-desc-box')].find(e=>e.__vue__?.$refs?.inputEditor===editor)?.__vue__;
      desc?.updateDescData?.();
    }
    if ('title' in command) input(title,command.title);
  } else if (command.action === 'options-link-url') {
    input(one('.post-with-link .link-input-wrap input[type=text]'),command.url);
  } else if (command.action === 'options-schedule-mode') {
    const radio=one('.post-time-wrap input[type=radio][value="'+(command.enabled?'1':'0')+'"]');
    if (!radio.checked) radio.click();
  } else if (command.action === 'options-schedule-time') {
    const picker=one('.post-time-wrap .weui-desktop-picker__date-time').__vue__;
    const timestamp=Date.parse(command.value);
    if (!picker || !Number.isFinite(timestamp) || timestamp < picker.minTime || timestamp > picker.maxTime)
      throw Error('channels_schedule_out_of_range');
    // The observed date-picker emits input(milliseconds) to its normal v-model listener.
    picker.$emit('input',timestamp);
  } else if (command.action === 'options-original') {
    const dialogs=all('[role=dialog],.weui-desktop-dialog__wrp').filter(visible);
    if(dialogs.length&&(command.value!==true||dialogs.length!==1||!originalDialog(dialogs[0])))throw Error('channels_dialog_open');
    state.originalityRequested=command.value===true;
    const checkbox=one('.declare-original-checkbox input[type=checkbox]');
    if (checkbox.checked !== command.value) checkbox.click();
  } else if (command.action === 'options-original-confirm') {
    if(command.value!==true||state.originalityRequested!==true||command.acceptTerms!==true)throw Error('channels_originality_not_requested');
    const dialogs=all('[role=dialog],.weui-desktop-dialog__wrp').filter(visible);
    if(dialogs.length!==1||!originalDialog(dialogs[0]))throw Error('channels_dialog_open');
    const dialog=dialogs[0],terms=unique([...dialog.querySelectorAll('input[type=checkbox]')]);
    // The shipped OriginalDeclare component owns checkProto and confirmDeclare.
    // Its native checkbox can diverge from its controlled model during an iframe
    // event transition; use the component's normal text/callback handlers.
    let controller=null;
    for(const element of all('.declare-original-dialog')){
      if(element!==dialog&&!element.contains(dialog)&&!dialog.contains(element))continue;
      for(let v=element.__vue__,i=0;v&&i<8;v=v.$parent,i++){
        if(typeof v.confirmDeclare==='function'&&typeof v.checkProto==='boolean'){controller=v;break;}
      }
      if(controller)break;
    }
    if(controller){
      if(controller.checkProto!==true){
        const label=unique([...dialog.querySelectorAll('.protocol-text')].filter(visible));
        label.click();return snapshot();
      }
      if(!terms.checked)throw Error('channels_originality_not_ready');
      controller.confirmDeclare();
      state.originalityConfirmed=controller.visible===false;
    }else{
      if(!terms.checked){terms.click();return snapshot();}
      const confirm=unique([...dialog.querySelectorAll('button')].filter(e=>visible(e)&&text(e)==='声明原创'));
      if(confirm.disabled||confirm.getAttribute('aria-disabled')==='true'||confirm.classList.contains('weui-desktop-btn_disabled'))throw Error('channels_originality_not_ready');
      confirm.click();state.originalityConfirmed=!snapshot().blockedByDialog;
    }
  } else if (command.action === 'options-native') {
    if (command.target==='cover') one('.cover-preview-wrap .vertical-cover-wrap .edit-btn').click();
    else if (command.target==='mention') unique(all('button,div,span').filter(e=>visible(e)&&e.children.length===0&&text(e)==='@视频号')).click();
    else if (command.target==='topic') unique(all('button,div,span').filter(e=>visible(e)&&e.children.length===0&&text(e)==='#话题')).click();
    else throw Error('channels_invalid_action');
  } else throw Error('channels_invalid_action');
  return snapshot();
}
