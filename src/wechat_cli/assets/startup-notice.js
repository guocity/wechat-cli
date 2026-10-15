// Suppress only WeChat's installation-location notice. Installed before Gadget resumes main().
// Returning the normal OK response lets WeChat persist its own suppression preference.
(() => {
  const policy = globalThis.WECHAT_INSTALL_NOTICE = {installed: false, suppressed: 0};
  const keep = [];
  policy.callbacks = keep;
  try {
    const exported = name => Module.getGlobalExportByName(name);
    const getClass = new NativeFunction(exported('objc_getClass'), 'pointer', ['pointer']);
    const getSelector = new NativeFunction(exported('sel_registerName'), 'pointer', ['pointer']);
    const instanceMethod = new NativeFunction(exported('class_getInstanceMethod'), 'pointer', ['pointer', 'pointer']);
    const implementation = new NativeFunction(exported('method_getImplementation'), 'pointer', ['pointer']);
    const msg = new NativeFunction(exported('objc_msgSend'), 'pointer', ['pointer', 'pointer']);
    const setState = new NativeFunction(exported('objc_msgSend'), 'void', ['pointer', 'pointer', 'long']);
    const selector = name => getSelector(Memory.allocUtf8String(name));
    const selectors = Object.fromEntries(['messageText', 'informativeText', 'UTF8String', 'suppressionButton'].map(name => [name, selector(name)]));
    const stateSelector = selector('setState:');
    const alertClass = getClass(Memory.allocUtf8String('NSAlert'));
    if (alertClass.isNull()) return;
    function text(object, field) {
      const string = msg(object, selectors[field]);
      if (string.isNull()) return '';
      const bytes = msg(string, selectors.UTF8String);
      return bytes.isNull() ? '' : bytes.readUtf8String();
    }
    function matches(alert) {
      try {
        const title = text(alert, 'messageText').replace(/Weixin/g, 'WeChat').trim();
        const body = text(alert, 'informativeText').replace(/Weixin/g, 'WeChat').trim();
        const english = title === 'WeChat was opened from the installation package'
          && body === 'We recommend you move WeChat to the Applications folder before using it.';
        const chinese = ['微信从安装包中打开', '微信是从安装包中打开的', '微信从安装包打开'].includes(title)
          && /建议.*微信.*应用程序/.test(body);
        if (!english && !chinese) return false;
        const checkbox = msg(alert, selectors.suppressionButton);
        if (!checkbox.isNull()) setState(checkbox, stateSelector, 1);
        policy.suppressed++;
        return true;
      } catch (_) { return false; }
    }
    function method(name) {
      const value = instanceMethod(alertClass, selector(name));
      return value.isNull() ? null : implementation(value);
    }
    const modal = method('runModal');
    if (modal) {
      const original = new NativeFunction(modal, 'long', ['pointer', 'pointer']);
      const replacement = new NativeCallback((self, cmd) => matches(self) ? 1000 : original(self, cmd),
        'long', ['pointer', 'pointer']);
      Interceptor.replace(modal, replacement); keep.push(replacement, original);
    }
    const sheet = method('beginSheetModalForWindow:completionHandler:');
    if (sheet) {
      const original = new NativeFunction(sheet, 'void', ['pointer', 'pointer', 'pointer', 'pointer']);
      const copyBlock = new NativeFunction(exported('_Block_copy'), 'pointer', ['pointer']);
      const releaseBlock = new NativeFunction(exported('_Block_release'), 'void', ['pointer']);
      const queueGetter = Module.findGlobalExportByName('dispatch_get_main_queue');
      const mainQueue = queueGetter ? new NativeFunction(queueGetter, 'pointer', []) : () => exported('_dispatch_main_q');
      const dispatch = new NativeFunction(exported('dispatch_async_f'), 'void', ['pointer', 'pointer', 'pointer']);
      const complete = new NativeCallback(block => {
        try {
          const callback = new NativeFunction(block.add(16).readPointer(), 'void', ['pointer', 'long']);
          callback(block, 1000);
        } finally { releaseBlock(block); }
      }, 'void', ['pointer']);
      const replacement = new NativeCallback((self, cmd, window, block) => {
        if (!matches(self)) return original(self, cmd, window, block);
        if (!block.isNull()) dispatch(mainQueue(), copyBlock(block), complete);
      }, 'void', ['pointer', 'pointer', 'pointer', 'pointer']);
      Interceptor.replace(sheet, replacement); keep.push(complete, replacement, original);
    }
    policy.installed = Boolean(modal || sheet);
    // Keep callbacks alive for the lifetime of this script.
    policy.callbacks = keep;
  } catch (_) {
    // If AppKit differs, preserve normal app behavior instead of dismissing an unknown alert.
  }
})();
