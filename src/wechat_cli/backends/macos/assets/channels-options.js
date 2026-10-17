// Page controls only; no direct store mutation and no agreement acceptance.
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
    const checkbox=one('.declare-original-checkbox input[type=checkbox]');
    if (checkbox.checked !== command.value) checkbox.click();
    // Any resulting terms dialog is left for the user; never click its agreement checkbox.
  } else if (command.action === 'options-native') {
    if (command.target==='cover') one('.cover-preview-wrap .vertical-cover-wrap .edit-btn').click();
    else if (command.target==='mention') unique(all('button,div,span').filter(e=>visible(e)&&e.children.length===0&&text(e)==='@视频号')).click();
    else if (command.target==='topic') unique(all('button,div,span').filter(e=>visible(e)&&e.children.length===0&&text(e)==='#话题')).click();
    else throw Error('channels_invalid_action');
  } else throw Error('channels_invalid_action');
  return snapshot();
}
