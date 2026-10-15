import AppKit
import Darwin
import ApplicationServices
import Foundation

// Operates only the configured WeChat process, through its own native controls.
// Never grants permissions. Send confirmation is scoped to the selected file dialog.
struct ControlError: Error { let code: String }
var fileSendStarted = false
let request = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String:Any]
func emit(_ value: [String:Any]) { let data = try! JSONSerialization.data(withJSONObject:value, options:[.sortedKeys]); FileHandle.standardOutput.write(data) }
func attribute(_ element: AXUIElement, _ name: String) -> AnyObject? {
    var value: CFTypeRef?; guard AXUIElementCopyAttributeValue(element,name as CFString,&value) == .success else { return nil }; return value
}
func string(_ element: AXUIElement, _ name: String) -> String { attribute(element,name) as? String ?? "" }
func elements(_ root: AXUIElement) -> [AXUIElement] {
    var output: [AXUIElement] = [], pending = [root], visited = 0
    while let element = pending.popLast(), visited < 6000 {
        visited += 1; output.append(element)
        if let children = attribute(element,kAXChildrenAttribute) as? [AXUIElement] { pending.append(contentsOf:children.reversed()) }
    }
    return output
}
func label(_ e: AXUIElement) -> String { [kAXTitleAttribute,kAXDescriptionAttribute,kAXHelpAttribute].map{string(e,$0)}.filter{!$0.isEmpty}.joined(separator:" ") }
func press(_ e: AXUIElement) throws {
    if let enabled = attribute(e,kAXEnabledAttribute) as? Bool, !enabled { throw ControlError(code:"native_control_disabled") }
    var pid:pid_t=0;guard AXUIElementGetPid(e,&pid) == .success else { throw ControlError(code:"native_control_action_failed") }
    guard let position=attribute(e,kAXPositionAttribute),let size=attribute(e,kAXSizeAttribute),CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID() else { throw ControlError(code:"native_control_action_failed") }
    var origin=CGPoint.zero,extent=CGSize.zero
    guard AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent),extent.width>0,extent.height>0 else { throw ControlError(code:"native_control_action_failed") }
    let point=CGPoint(x:origin.x+extent.width/2,y:origin.y+extent.height/2)
    guard let down=CGEvent(mouseEventSource:nil,mouseType:.leftMouseDown,mouseCursorPosition:point,mouseButton:.left),let up=CGEvent(mouseEventSource:nil,mouseType:.leftMouseUp,mouseCursorPosition:point,mouseButton:.left) else { throw ControlError(code:"native_control_action_failed") }
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { throw ControlError(code:"native_app_not_frontmost") }
    CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.08)
    down.post(tap:.cghidEventTap);Thread.sleep(forTimeInterval:0.05);up.post(tap:.cghidEventTap)
}
func scrollHistoryUp(_ root:AXUIElement,_ pid:Int32) throws {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
          let list=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="chat_message_list"}),
          let position=attribute(list,kAXPositionAttribute),let size=attribute(list,kAXSizeAttribute) else { throw ControlError(code:"native_attachment_not_visible") }
    var origin=CGPoint.zero,extent=CGSize.zero
    guard CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID(),
          AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent),extent.width>0,extent.height>0 else { throw ControlError(code:"native_attachment_not_visible") }
    let point=CGPoint(x:origin.x+extent.width/2,y:origin.y+extent.height/2)
    CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.05)
    CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:600,wheel2:0,wheel3:0)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.15)
}
func find(_ root: AXUIElement, _ names: [String]) throws -> AXUIElement {
    let matches = elements(root).filter { e in
        string(e,kAXRoleAttribute) == kAXButtonRole && names.contains { label(e) == $0 || label(e).hasPrefix($0+"(") || label(e).hasPrefix($0+" (") }
    }
    guard let first = matches.last else { throw ControlError(code:"native_control_not_found") }; return first
}
func state(_ root: AXUIElement) -> [String:Any] {
    let list = elements(root)
    let draft = list.first { string($0,kAXIdentifierAttribute) == "chat_input_field" }
    let focusedWindow=attribute(root,kAXFocusedWindowAttribute)
    let fileUiReady=focusedWindow.map{elements($0 as! AXUIElement).contains{string($0,kAXIdentifierAttribute)=="chat_input_field"}} ?? false
    let title = list.first { string($0,kAXIdentifierAttribute) == "current_chat_name_label" }
    // Return only active feature controls, not conversation/message contents.
    let names = list.filter { string($0,kAXRoleAttribute) == kAXButtonRole }.map{label($0)}
    let featureNames = names.filter { value in ["Voice Input","Send Voice","Cancel","Done","Finish","Stop","Start","Import","语音","取消","完成","停止","开始","导入"].contains{value.hasPrefix($0)} }
    return ["accessibility":true,"fileUiReady":fileUiReady,"chatName":title.map{string($0,kAXValueAttribute)} ?? "","draft":draft.map{string($0,kAXValueAttribute)} ?? "","controls":Array(Set(featureNames)).sorted()]
}

func key(_ pid: Int32, _ code: CGKeyCode, _ flags: CGEventFlags = []) throws {
    guard let down = CGEvent(keyboardEventSource:nil,virtualKey:code,keyDown:true), let up = CGEvent(keyboardEventSource:nil,virtualKey:code,keyDown:false) else { throw ControlError(code:"native_control_failed") }
    down.flags=flags;up.flags=flags;guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { throw ControlError(code:"native_app_not_frontmost") };down.post(tap:.cghidEventTap);up.post(tap:.cghidEventTap)
}
func type(_ pid:Int32, _ value:String) throws {
    let chars=Array(value.utf16)
    guard let event=CGEvent(keyboardEventSource:nil,virtualKey:0,keyDown:true) else { throw ControlError(code:"native_control_failed") }
    chars.withUnsafeBufferPointer { event.keyboardSetUnicodeString(stringLength:chars.count,unicodeString:$0.baseAddress!) }
    event.postToPid(pid);event.type = .keyUp;event.postToPid(pid)
}
func waitFor(_ root:AXUIElement,_ names:[String],_ seconds:Double=3) throws -> AXUIElement {
    let end=Date().addingTimeInterval(seconds)
    repeat { if let button=try? find(root,names) { return button };Thread.sleep(forTimeInterval:0.1) } while Date()<end
    throw ControlError(code:"native_control_not_found")
}
func sendFile(_ root:AXUIElement,_ pid:Int32,_ path:String,_ name:String) throws {
    let expectedChat=state(root)["chatName"] as? String ?? ""
    // A blank draft and exact chat match are checked before entering the picker.
    guard (state(root)["draft"] as? String ?? "").isEmpty else { throw ControlError(code:"native_draft_not_empty") }
    try press(find(root,["Send File","发送文件"]))
    _ = try waitFor(root,["Open","打开","Choose","选择"])
    try key(pid,5,[.maskCommand,.maskShift]) // Native NSOpenPanel Go to Folder.
    Thread.sleep(forTimeInterval:0.15)
    let fields=elements(root).filter { string($0,kAXRoleAttribute)==kAXTextFieldRole || string($0,kAXRoleAttribute)==kAXComboBoxRole }
    guard let field=fields.last else { throw ControlError(code:"native_file_picker_not_found") }
    guard AXUIElementSetAttributeValue(field,kAXValueAttribute as CFString,path as CFString) == .success else { throw ControlError(code:"native_file_picker_not_found") }
    try key(pid,36);Thread.sleep(forTimeInterval:0.15)
    // Selecting a file can auto-send in some builds. An error after this action
    // is an unknown send outcome, never a promise that nothing was sent.
    do {
        guard state(root)["chatName"] as? String == expectedChat else { throw ControlError(code:"native_active_chat_mismatch") }
        guard (state(root)["draft"] as? String ?? "").isEmpty else { throw ControlError(code:"native_draft_not_empty") }
        fileSendStarted=true
        try press(waitFor(root,["Open","打开","Choose","选择"]))
        Thread.sleep(forTimeInterval:0.3)
        let windows=attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? []
        let sheets=windows.flatMap{elements($0).filter{string($0,kAXRoleAttribute)==kAXSheetRole}}
        for window in windows+sheets {
            let list=elements(window)
            let containsName=list.contains { label($0).contains(name) || string($0,kAXValueAttribute).contains(name) }
            let title=string(window,kAXTitleAttribute)
            if containsName && (title.contains("Send") || title.contains("发送") || string(window,kAXRoleAttribute)==kAXSheetRole || string(window,kAXSubroleAttribute)=="AXDialog") {
                if let button=try? find(window,["Send","发送"]) { try press(button);return }
            }
        }
        // This build inserts the selected file into the composer. Send only
        // that attachment-only draft in the same chat; never send user text.
        let after=state(root)
        if after["chatName"] as? String == expectedChat && after["draft"] as? String == "\u{fffc}" {
            try press(find(root,["Send","发送"]))
        }
    } catch { if fileSendStarted { throw ControlError(code:"native_send_outcome_unknown") };throw error }
}

do {
    guard AXIsProcessTrusted() else { throw ControlError(code:"native_accessibility_required") }
    guard let pid = request["pid"] as? Int32, let expected = request["app"] as? String,
          let app = NSRunningApplication(processIdentifier:pid) else { throw ControlError(code:"native_process_identity_mismatch") }
    // LaunchServices can report another copy's bundle URL when both share WeChat's bundle ID.
    // Verify the actual process executable instead, resolving /tmp aliases on both sides.
    var pathBuffer=[CChar](repeating:0,count:4096)
    let pathLength=proc_pidpath(pid,&pathBuffer,UInt32(pathBuffer.count))
    let actualPath=String(cString:pathBuffer)
    let expectedPath=URL(fileURLWithPath:expected).appendingPathComponent("Contents/MacOS/WeChat").resolvingSymlinksInPath().path
    guard pathLength>0, URL(fileURLWithPath:actualPath).resolvingSymlinksInPath().path == expectedPath else { throw ControlError(code:"native_process_identity_mismatch") }
    let root = AXUIElementCreateApplication(pid)
    let action = request["action"] as? String ?? "status"
    if action != "status" && action != "attachment.status" {
        _ = NSApplication.shared
        if let window=attribute(root,kAXMainWindowAttribute) { AXUIElementPerformAction(window as! AXUIElement,kAXRaiseAction as CFString) }
        app.activate(options:[.activateIgnoringOtherApps])
        let deadline=Date().addingTimeInterval(3)
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid && Date()<deadline { RunLoop.current.run(until:Date().addingTimeInterval(0.1)) }
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { throw ControlError(code:"native_app_not_frontmost") }
    }
    if let chatName = request["chatName"] as? String, !chatName.isEmpty {
        guard state(root)["chatName"] as? String == chatName else { throw ControlError(code:"native_active_chat_mismatch") }
    }
    if action == "dictation.start" || action == "recording.start" {
        guard let composer=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="chat_input_field"}) else { throw ControlError(code:"native_control_not_found") }
        try press(composer)
        AXUIElementSetAttributeValue(composer,kAXFocusedAttribute as CFString,kCFBooleanTrue)
    }
    switch action {
    case "status": break
    case "file.send":
        guard let path=request["filePath"] as? String,let name=request["fileName"] as? String,path.hasPrefix("/"),!name.isEmpty else { throw ControlError(code:"invalid_input") }
        try sendFile(root,pid,path,name)
    case "attachment.status":
        guard let name=request["fileName"] as? String,!name.isEmpty else { throw ControlError(code:"invalid_input") }
        for window in attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            let list=elements(window)
            if list.contains(where:{label($0).contains(name)||string($0,kAXValueAttribute).contains(name)}) &&
               list.contains(where:{ e in ["File cleared","File expired","文件已清理","文件已过期"].contains { label(e).contains($0)||string(e,kAXValueAttribute).contains($0) } }) {
                throw ControlError(code:"native_attachment_unavailable")
            }
        }
    case "attachment.download":
        guard let name=request["fileName"] as? String,!name.isEmpty else { throw ControlError(code:"invalid_input") }
        var target:AXUIElement?
        for _ in 0..<20 {
            guard state(root)["chatName"] as? String == request["chatName"] as? String else { throw ControlError(code:"native_active_chat_mismatch") }
            let matches=elements(root).filter { e in
                string(e,kAXIdentifierAttribute)=="chat_bubble_item_view" && elements(e).contains { child in
                    [label(child),string(child,kAXValueAttribute)].contains { $0.components(separatedBy:"\n").contains { $0.trimmingCharacters(in:.whitespaces)==name } }
                }
            }
            if matches.count>1 { throw ControlError(code:"native_attachment_ambiguous") }
            if let match=matches.first {target=match;break}
            try scrollHistoryUp(root,pid)
        }
        guard let target=target else { throw ControlError(code:"native_attachment_not_visible") }
        try press(target)
        // A file bubble can open WeChat's attachment window first. Activate
        // only its Download control, scoped to the requested filename.
        Thread.sleep(forTimeInterval:0.25)
        for window in attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            let scoped=elements(window).contains { label($0).contains(name)||string($0,kAXValueAttribute).contains(name) }
            let main=elements(window).contains { string($0,kAXIdentifierAttribute)=="chat_input_field" }
            if scoped && !main,let download=try? find(window,["Download","下载"]) {try press(download);break}
        }
    case "recording.send":
        // Only a voice recording window can provide this Send button.
        let windows=attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? []
        guard let window=windows.first(where:{string($0,kAXTitleAttribute).contains("Voice") || string($0,kAXTitleAttribute).contains("语音")}) else { throw ControlError(code:"native_control_not_found") }
        try press(find(window,["Send","发送"]))
    case "dictation.start":
        let e=CGEvent(keyboardEventSource:nil,virtualKey:63,keyDown:true)!;e.type = .flagsChanged;e.flags = .maskSecondaryFn;e.post(tap:.cghidEventTap)
    case "dictation.stop":
        let e=CGEvent(keyboardEventSource:nil,virtualKey:63,keyDown:false)!;e.type = .flagsChanged;e.flags = [];e.post(tap:.cghidEventTap)
    case "dictation.cancel":
        try key(pid,53);let e=CGEvent(keyboardEventSource:nil,virtualKey:63,keyDown:false)!;e.type = .flagsChanged;e.flags = [];e.post(tap:.cghidEventTap)
    case "recording.cancel":
        try key(pid,53);let e=CGEvent(keyboardEventSource:nil,virtualKey:61,keyDown:false)!;e.type = .flagsChanged;e.flags = [];e.post(tap:.cghidEventTap)
    case "recording.start":
        let e=CGEvent(keyboardEventSource:nil,virtualKey:61,keyDown:true)!;e.type = .flagsChanged;e.flags = CGEventFlags(rawValue:CGEventFlags.maskAlternate.rawValue | 0x40);e.post(tap:.cghidEventTap)
    case "recording.stop":
        let e=CGEvent(keyboardEventSource:nil,virtualKey:61,keyDown:false)!;e.type = .flagsChanged;e.flags = [];e.post(tap:.cghidEventTap)
    case "import.start": try press(find(root,["Import chat history from phone","从手机导入聊天记录"]))
    case "import.cancel":
        let windows=attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? []
        guard let window=windows.first(where:{["Import Chat History","导入聊天记录"].contains(string($0,kAXTitleAttribute))}),let close=attribute(window,kAXCloseButtonAttribute) else { throw ControlError(code:"native_control_not_found") }
        guard AXUIElementPerformAction(close as! AXUIElement,kAXPressAction as CFString) == .success else { throw ControlError(code:"native_control_action_failed") }
    default: throw ControlError(code:"invalid_native_control")
    }
    // WeChat changes controls asynchronously; observe without synthesizing state.
    if action != "status" && action != "attachment.status" { Thread.sleep(forTimeInterval:0.15) }
    emit(state(root))
} catch let error as ControlError { emit(["error":error.code,"possibleSend":fileSendStarted]) }
catch { emit(["error":"native_control_failed"]) }
