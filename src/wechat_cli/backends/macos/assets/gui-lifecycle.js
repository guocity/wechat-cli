// Observe native window closing without changing WeChat's close behavior.
// Miniaturizing, hiding the app, and closing an auxiliary window do not stop the server.
(() => {
  const policy = globalThis.WECHAT_GUI_LIFECYCLE = {installed: false, primaryVisible: 0, seen: false};
  const callbacks = [];
  policy.callbacks = callbacks;
  const exp = name => Module.getGlobalExportByName(name);
  const cls = new NativeFunction(exp('objc_getClass'), 'pointer', ['pointer']);
  const objectClass = new NativeFunction(exp('object_getClass'), 'pointer', ['pointer']);
  const sel = new NativeFunction(exp('sel_registerName'), 'pointer', ['pointer']);
  const method = new NativeFunction(exp('class_getInstanceMethod'), 'pointer', ['pointer', 'pointer']);
  const imp = new NativeFunction(exp('method_getImplementation'), 'pointer', ['pointer']);
  const selector = name => sel(Memory.allocUtf8String(name));
  const msg = new NativeFunction(exp('objc_msgSend'), 'pointer', ['pointer', 'pointer']);
  const signed = new NativeFunction(exp('objc_msgSend'), 'long', ['pointer', 'pointer']);
  const integer = (...args) => Number(signed(...args));
  const item = new NativeFunction(exp('objc_msgSend'), 'pointer', ['pointer', 'pointer', 'ulong']);
  const retain = new NativeFunction(exp('objc_retain'), 'pointer', ['pointer']);
  const release = new NativeFunction(exp('objc_release'), 'void', ['pointer']);
  const title = selector('title'), utf8 = selector('UTF8String'), visible = selector('isVisible');
  const main = selector('isMainWindow'), minimized = selector('isMiniaturized'), hidden = selector('isHidden');
  const known = new Set();
  const hooked = new Set();
  const closeIntents = new Map();
  const level = selector('level'), windows = selector('windows'), count = selector('count'), at = selector('objectAtIndex:');
  const dispatch = new NativeFunction(exp('dispatch_async_f'), 'void', ['pointer', 'pointer', 'pointer']);
  const queue = exp('_dispatch_main_q');
  let timer = null, reported = false, absentSince = null, disposed = false;
  function observe(address, argument, explicit) {
    const key = address.toString();
    if (hooked.has(key)) return;
    hooked.add(key);
    callbacks.push(Interceptor.attach(address, {
      onEnter(args) {
        if (disposed) { this.primary = false; this.target = null; return; }
        try { this.primary = primary(args[argument]) && Boolean(integer(args[argument], visible)); }
        catch (_) { this.primary = false; }
        const key = args[argument].toString();
        if (this.primary && explicit) closeIntents.set(key, Date.now());
        // Qt may defer orderOut until after its close delegate returns.
        const recentClose = Date.now() - (closeIntents.get(key) ?? 0) < 1000;
        this.target = this.primary && (explicit || recentClose) ? retain(args[argument]) : null;
      },
      onLeave() {
        if (this.target) {
          try {
            const app = exp('NSApp').readPointer();
            if (!disposed && !integer(this.target, visible) && !app.isNull() && visibleCount(app) === 0) reportClosed();
          } finally { release(this.target); }
        }
        if (!this.primary || disposed) return;
        if (timer !== null) clearTimeout(timer);
        timer = setTimeout(() => dispatch(queue, ptr(0), check), 1000);
      }
    }));
  }
  function observeClass(type, name, argument, explicit) {
    const entry = method(type, selector(name));
    if (!entry.isNull()) observe(imp(entry), argument, explicit);
  }
  function observeWindow(window) {
    const type = objectClass(window);
    observeClass(type, 'close', 0, false);
    observeClass(type, 'performClose:', 0, true);
    observeClass(type, 'orderOut:', 0, false);
    const delegate = msg(window, selector('delegate'));
    if (!delegate.isNull()) observeClass(objectClass(delegate), 'windowShouldClose:', 2, true);
  }
  function primary(window) {
    if (integer(window, level) !== 0) return false;
    const name = msg(msg(window, title), utf8);
    const key = window.toString();
    const matches = !name.isNull() && ['WeChat', 'Weixin', '微信'].includes(name.readUtf8String());
    if (matches || integer(window, main)) known.add(key);
    return known.has(key);
  }
  function visibleCount(app) {
    const list = msg(app, windows);
    let open = 0;
    for (let i = 0; i < Math.min(integer(list, count), 256); i++) {
      const window = item(list, at, i);
      if (primary(window)) {
        observeWindow(window);
        if (integer(window, visible) || integer(window, minimized)) open++;
      }
    }
    return open;
  }
  function reportClosed() {
    if (!reported) { reported = true; send({event: 'gui_closed'}); }
  }
  const check = new NativeCallback(() => {
    if (disposed) return;
    try {
      const app = exp('NSApp').readPointer();
      if (app.isNull()) return;
      const open = visibleCount(app);
      policy.primaryVisible = open;
      if (open) { policy.seen = true; absentSince = null; return; }
      // Cmd-H and minimization preserve the session. Qt can close a window
      // without invoking NSWindow.close, so also observe visibility.
      if (!policy.seen || integer(app, hidden)) { absentSince = null; return; }
      if (absentSince === null) { absentSince = Date.now(); return; }
      if (!reported && Date.now() - absentSince >= 1000) {
        reportClosed();
      }
    } catch (_) {}
  }, 'void', ['pointer']);
  callbacks.push(check);
  const windowClass = cls(Memory.allocUtf8String('NSWindow'));
  for (const name of ['close', 'performClose:']) {
    observeClass(windowClass, name, 0, name === 'performClose:');
    policy.installed = true;
  }
  const interval = setInterval(() => dispatch(queue, ptr(0), check), 250);
  policy.dispose = () => new Promise(resolve => {
    disposed = true;
    clearInterval(interval);
    if (timer !== null) clearTimeout(timer);
    // Drain queued main-thread callbacks before the Frida script is unloaded.
    const drained = new NativeCallback(() => resolve(), 'void', ['pointer']);
    callbacks.push(drained);
    dispatch(queue, ptr(0), drained);
  });
})();
