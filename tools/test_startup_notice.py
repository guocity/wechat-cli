"""Opt-in macOS integration test for the exact startup-notice hook. Never opens the user's WeChat."""

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
from wechat_cli.config import ASSETS

FIXTURE = r'''
#import <AppKit/AppKit.h>
#include <cstdio>
int main(int argc,char **argv){
 @autoreleasepool {
  [NSApplication sharedApplication];
  NSAlert *alert=[NSAlert new];
  const bool unrelated=argc>1 && strcmp(argv[1],"unrelated")==0;
  alert.messageText=unrelated?@"Unrelated fixture notice":@"WeChat was opened from the installation package";
  alert.informativeText=@"We recommend you move WeChat to the Applications folder before using it.";
  alert.showsSuppressionButton=YES;
  alert.suppressionButton.state=NSControlStateValueOff;
  [alert addButtonWithTitle:@"OK"];
  if(argc>1 && strcmp(argv[1],"sheet")==0){
   NSWindow *window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,200,100) styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
   [alert beginSheetModalForWindow:window completionHandler:^(NSModalResponse response){
    printf("{\"response\":%ld,\"remembered\":%ld}\n",(long)response,(long)alert.suppressionButton.state);fflush(stdout);exit(0);
   }];
   [NSApp run];
  }else{
   if(unrelated)dispatch_after(dispatch_time(DISPATCH_TIME_NOW,200*NSEC_PER_MSEC),dispatch_get_main_queue(),^{[NSApp abortModal];});
   NSInteger response=[alert runModal];
   printf("{\"response\":%ld,\"remembered\":%ld}\n",(long)response,(long)alert.suppressionButton.state);
  }
 }
 return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gadget', required=True, type=Path)
    args = parser.parse_args()
    results = {}
    with tempfile.TemporaryDirectory(prefix='wechat-notice-test-') as directory:
        root = Path(directory)
        shutil.copyfile(args.gadget, root / 'FridaGadget.dylib')
        source, binary = root / 'fixture.mm', root / 'fixture'
        source.write_text(FIXTURE)
        subprocess.run(['/usr/bin/clang++', '-fobjc-arc', '-framework', 'AppKit', str(source), '-o', str(binary)], check=True)
        for mode in ['modal', 'sheet', 'unrelated']:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            token = 'startup-notice-fixture-token-1234'
            (root / 'FridaGadget.config').write_text(json.dumps({'interaction': {'type':'listen','address':'127.0.0.1','port':port,'token':token,'on_load':'wait'}}))
            process = subprocess.Popen([str(binary), mode], env={**os.environ, 'DYLD_INSERT_LIBRARIES': str(root / 'FridaGadget.dylib')}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            manager = frida.get_device_manager()
            endpoint = f'127.0.0.1:{port}'
            device = manager.add_remote_device(endpoint, token=token)
            session = None
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        session = device.attach('Gadget')
                        break
                    except frida.ServerNotRunningError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.1)
                script = session.create_script((ASSETS / 'startup-notice.js').read_text())
                script.load()
                device.resume(process.pid)
                output, error = process.communicate(timeout=15)
                if process.returncode:
                    raise RuntimeError(f'Fixture failed: {error[:1000]}')
                value = json.loads(next(line for line in reversed(output.splitlines()) if line.startswith("{")))
                if mode == 'unrelated':
                    assert value['response'] != 1000 and value['remembered'] == 0, value
                else:
                    assert value == {'response':1000,'remembered':1}, value
                results[mode] = value
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if session:
                    try:
                        session.detach()
                    except frida.InvalidOperationError:
                        pass
                manager.remove_remote_device(endpoint)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
