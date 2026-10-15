"""Opt-in real AppKit/Frida fixtures; never controls the user's WeChat."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import frida

from wechat_cli.backends.macos.setup import ASSETS

FIXTURE = r"""
#import <AppKit/AppKit.h>
@interface FixtureWindow : NSWindow
@end
@implementation FixtureWindow
- (void)performClose:(id)sender {
 dispatch_after(dispatch_time(DISPATCH_TIME_NOW,100*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[self orderOut:sender];});
}
@end
int main(int argc,char **argv){
 @autoreleasepool {
  [NSApplication sharedApplication];
  [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
  NSString *mode=[NSString stringWithUTF8String:argv[1]];
  NSWindow *(^make)(NSString *)=^NSWindow *(NSString *title){
   Class type=[mode isEqualToString:@"subclass-close"] ? [FixtureWindow class] : [NSWindow class];
   NSWindow *window=[[type alloc] initWithContentRect:NSMakeRect(20,20,320,180)
    styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable)
    backing:NSBackingStoreBuffered defer:NO];
   window.releasedWhenClosed=NO;window.title=title;[window makeKeyAndOrderFront:nil];return window;
  };
  NSWindow *window=make(@"WeChat");
  dispatch_after(dispatch_time(DISPATCH_TIME_NOW,1200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
   if([mode isEqualToString:@"close"] || [mode isEqualToString:@"subclass-close"]) [window performClose:nil];
   if([mode isEqualToString:@"reopen"]) { [window performClose:nil];make(@"WeChat"); }
   if([mode isEqualToString:@"qt-hide"]) [window orderOut:nil];
   if([mode isEqualToString:@"minimize"]) [window miniaturize:nil];
   if([mode isEqualToString:@"hide"]) [NSApp hide:nil];
   if([mode isEqualToString:@"auxiliary"]) { NSWindow *other=make(@"Auxiliary fixture");[other close]; }
   if([mode isEqualToString:@"transition"]) {
    [window close];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(),^{make(@"WeChat");});
   }
  });
  [NSApp run];
 }
 return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gadget", required=True, type=Path)
    args = parser.parse_args()
    results = {}
    with tempfile.TemporaryDirectory(prefix="wechat-gui-test-") as temporary:
        root = Path(temporary)
        gadget = root / "FridaGadget.dylib"
        shutil.copyfile(args.gadget, gadget)
        source, binary = root / "fixture.mm", root / "fixture"
        source.write_text(FIXTURE)
        subprocess.run(
            ["/usr/bin/clang++", "-fobjc-arc", "-framework", "AppKit", str(source), "-o", str(binary)],
            check=True,
        )
        for mode in [
            "close",
            "subclass-close",
            "reopen",
            "qt-hide",
            "minimize",
            "hide",
            "auxiliary",
            "transition",
        ]:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            token = "gui-fixture-token-123456789"
            (root / "FridaGadget.config").write_text(
                json.dumps(
                    {
                        "interaction": {
                            "type": "listen",
                            "address": "127.0.0.1",
                            "port": port,
                            "token": token,
                            "on_load": "wait",
                        }
                    }
                )
            )
            process = subprocess.Popen(
                [str(binary), mode],
                env={**os.environ, "DYLD_INSERT_LIBRARIES": str(gadget)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            manager = frida.get_device_manager()
            endpoint = f"127.0.0.1:{port}"
            device = manager.add_remote_device(endpoint, token=token)
            session = None
            messages = []
            try:
                deadline = time.monotonic() + 15
                while session is None:
                    try:
                        session = device.attach("Gadget")
                    except frida.ServerNotRunningError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                script = session.create_script(
                    (ASSETS / "gui-lifecycle.js").read_text()
                    + "\nrpc.exports={disposeGui(){return globalThis.WECHAT_GUI_LIFECYCLE.dispose();}};"
                )
                script.on("message", lambda message, data: messages.append(message))
                script.load()
                device.resume(process.pid)
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    time.sleep(0.05)
                assert process.poll() is None, "Fixture must remain running after window close"
                assert not [m for m in messages if m.get("type") == "error"], messages
                closed = any(m.get("payload") == {"event": "gui_closed"} for m in messages)
                assert closed == (mode in {"close", "subclass-close", "reopen", "qt-hide"}), (mode, messages)
                results[mode] = {"guiClosed": closed, "processStillRunning": True}
            finally:
                if session:
                    script.exports_sync.dispose_gui()
                    script.unload()
                    session.detach()
                process.terminate()
                process.wait(timeout=5)
                manager.remove_remote_device(endpoint)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
