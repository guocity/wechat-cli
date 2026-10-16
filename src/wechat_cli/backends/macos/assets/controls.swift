import AppKit
import Darwin
import ApplicationServices
import Foundation
import AVFoundation

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
func press(_ e: AXUIElement, targeted:Bool=false) throws {
    if let enabled = attribute(e,kAXEnabledAttribute) as? Bool, !enabled { throw ControlError(code:"native_control_disabled") }
    var pid:pid_t=0;guard AXUIElementGetPid(e,&pid) == .success else { throw ControlError(code:"native_control_action_failed") }
    guard let position=attribute(e,kAXPositionAttribute),let size=attribute(e,kAXSizeAttribute),CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID() else { throw ControlError(code:"native_control_action_failed") }
    var origin=CGPoint.zero,extent=CGSize.zero
    guard AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent),extent.width>0,extent.height>0 else { throw ControlError(code:"native_control_action_failed") }
    let point=CGPoint(x:origin.x+extent.width/2,y:origin.y+extent.height/2)
    let mouseSource=CGEventSource(stateID:.combinedSessionState)
    mouseSource?.localEventsSuppressionInterval=0
    guard let down=CGEvent(mouseEventSource:mouseSource,mouseType:.leftMouseDown,mouseCursorPosition:point,mouseButton:.left),let up=CGEvent(mouseEventSource:mouseSource,mouseType:.leftMouseUp,mouseCursorPosition:point,mouseButton:.left) else { throw ControlError(code:"native_control_action_failed") }
    down.setIntegerValueField(.mouseEventClickState,value:1)
    up.setIntegerValueField(.mouseEventClickState,value:1)
    down.setDoubleValueField(.mouseEventPressure,value:1)
    up.setDoubleValueField(.mouseEventPressure,value:0)
    down.flags=[];up.flags=[]
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { throw ControlError(code:"native_app_not_frontmost") }
    CGWarpMouseCursorPosition(point)
    CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.08)
    if targeted {
        down.postToPid(pid);Thread.sleep(forTimeInterval:0.05);up.postToPid(pid)
    } else {
        down.post(tap:.cghidEventTap);Thread.sleep(forTimeInterval:0.1);up.post(tap:.cghidEventTap)
    }
    RunLoop.current.run(until:Date().addingTimeInterval(0.2))
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
        string(e,kAXRoleAttribute) == kAXButtonRole && names.contains { name in
            [label(e),string(e,kAXTitleAttribute),string(e,kAXDescriptionAttribute),string(e,kAXHelpAttribute),string(e,kAXValueAttribute)].contains(name) || label(e).hasPrefix(name+"(") || label(e).hasPrefix(name+" ") || elements(e).dropFirst().contains { string($0,kAXRoleAttribute)==kAXStaticTextRole && string($0,kAXValueAttribute)==name }
        }
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
func openChannels(_ root:AXUIElement,_ pid:Int32) throws {
    guard let name=request["chatName"] as? String, !name.isEmpty,
          let caption=request["caption"] as? String, !caption.isEmpty,
          let author=request["author"] as? String, !author.isEmpty else { throw ControlError(code:"channels_card_identity_required") }
    func windows() -> [AXUIElement] { attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? [] }
    func history() -> AXUIElement? {
        windows().first { w in
            string(w,kAXTitleAttribute).contains("\""+name+"\"") &&
            elements(w).contains { string($0,kAXIdentifierAttribute)=="record_type_pagetab_5" }
        }
    }
    if history()==nil {
        var chat=windows().first { w in elements(w).contains { string($0,kAXIdentifierAttribute)=="current_chat_name_label" && string($0,kAXValueAttribute)==name } }
        if chat==nil {
            let sessions=elements(root).filter { string($0,kAXIdentifierAttribute)=="session_item_"+name }
            guard sessions.count==1 else { throw ControlError(code:"channels_chat_not_visible") }
            try press(sessions[0]);Thread.sleep(forTimeInterval:0.4)
            chat=windows().first { w in elements(w).contains { string($0,kAXIdentifierAttribute)=="current_chat_name_label" && string($0,kAXValueAttribute)==name } }
        }
        guard let chat=chat else { throw ControlError(code:"channels_chat_not_visible") }
        AXUIElementPerformAction(chat,kAXRaiseAction as CFString)
        try press(find(chat,["Chat History","聊天记录"]))
        let end=Date().addingTimeInterval(3)
        while history()==nil && Date()<end { Thread.sleep(forTimeInterval:0.1) }
    }
    guard let window=history() else { throw ControlError(code:"channels_history_not_found") }
    AXUIElementPerformAction(window,kAXRaiseAction as CFString)
    RunLoop.current.run(until:Date().addingTimeInterval(0.5))
    guard let focused=attribute(root,kAXFocusedWindowAttribute), CFEqual(focused,window) else { throw ControlError(code:"channels_history_not_focused") }
    guard let tab=elements(window).first(where:{string($0,kAXIdentifierAttribute)=="record_type_pagetab_5"}) else { throw ControlError(code:"channels_history_not_found") }
    try press(tab);Thread.sleep(forTimeInterval:0.4)
    func normalized(_ text:String)->String {text.split(whereSeparator:{$0.isWhitespace}).joined(separator:" ")}
    // The history window may retain its previous scroll position. Start at newest.
    if let list=elements(window).first(where:{string($0,kAXIdentifierAttribute)=="subtype_message_list"}),
       let position=attribute(list,kAXPositionAttribute),let size=attribute(list,kAXSizeAttribute) {
        var origin=CGPoint.zero,extent=CGSize.zero
        if CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID(),
           AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent) {
            let point=CGPoint(x:origin.x+extent.width/2,y:origin.y+extent.height/2)
            CGWarpMouseCursorPosition(point)
            CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
            CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:100000,wheel2:0,wheel3:0)?.post(tap:.cghidEventTap)
            Thread.sleep(forTimeInterval:0.4)
        }
    }
    var previous="", unchanged=0
    for _ in 0..<30 {
        guard let list=elements(window).first(where:{string($0,kAXIdentifierAttribute)=="subtype_message_list"}) else { throw ControlError(code:"channels_history_not_found") }
        let rows=(attribute(list,kAXChildrenAttribute) as? [AXUIElement] ?? [])
        let matches=rows.filter { row in
            let title=normalized(string(row,kAXTitleAttribute)+" "+string(row,kAXValueAttribute))
            let description=string(row,kAXDescriptionAttribute)
            return title.hasPrefix(normalized(caption)+" ") && description.components(separatedBy:" | ").first==author
        }
        guard matches.count<=1 else { throw ControlError(code:"channels_card_ambiguous") }
        if let card=matches.first {
            // History cards expose their own clickable frame, unlike full-width chat bubbles.
            try press(card)
            emit(["opened":true]);return
        }
        let fingerprint=rows.map{label($0)+string($0,kAXValueAttribute)}.joined(separator:"\n")
        unchanged=fingerprint==previous ? unchanged+1 : 0;previous=fingerprint
        if unchanged>=2 { break }
        guard let position=attribute(list,kAXPositionAttribute),let size=attribute(list,kAXSizeAttribute) else { break }
        var origin=CGPoint.zero,extent=CGSize.zero
        guard CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID(),
              AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent) else { break }
        let point=CGPoint(x:origin.x+extent.width/2,y:origin.y+extent.height/2)
        CGWarpMouseCursorPosition(point)
        CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
        CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:-500,wheel2:0,wheel3:0)?.post(tap:.cghidEventTap)
        Thread.sleep(forTimeInterval:0.2)
    }
    throw ControlError(code:"channels_card_not_found")
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

// Moments stays inside the vendor composer. Contact/tag selection, emoji and
// Xiaowei results remain interactive native UI, with no fabricated account data.
func momentElements(_ root:AXUIElement) -> [AXUIElement] {
    guard let focused=attribute(root,kAXFocusedWindowAttribute) else { return elements(root) }
    var scope=focused as! AXUIElement
    for _ in 0..<8 {
        let children=attribute(scope,kAXChildrenAttribute) as? [AXUIElement] ?? []
        let sheets=children.filter{string($0,kAXRoleAttribute)==kAXSheetRole}
        guard let next=sheets.last else { break }
        scope=next
    }
    return elements(scope)
}
func momentComposer(_ root:AXUIElement) -> AXUIElement? {
    // A floating Moments window can lose focus between CLI invocations.
    // Search all this process's windows, including sheets under their parents.
    elements(root).reversed().first { e in
        [kAXSheetRole,kAXWindowRole].contains(string(e,kAXRoleAttribute)) &&
        elements(e).contains{string($0,kAXRoleAttribute)==kAXTextAreaRole} &&
        (try? find(e,["Post","发表"])) != nil && (try? find(e,["Cancel","取消"])) != nil
    }
}
func waitMoment(_ root:AXUIElement) throws -> AXUIElement {
    let deadline=Date().addingTimeInterval(15)
    repeat {
        if let composer=momentComposer(root) { return composer }
        Thread.sleep(forTimeInterval:0.1)
    } while Date()<deadline
    throw ControlError(code:"moment_composer_not_found")
}
func momentState(_ root:AXUIElement) -> [String:Any] {
    guard let composer=momentComposer(root) else {
        let selecting=momentElements(root).contains{["sp_to_select_contact_list","chat_bot_message_list","open-panel"].contains(string($0,kAXIdentifierAttribute)) || ["All contacts","Just me"].contains(string($0,kAXDescriptionAttribute))}
        return ["source":"native","status":selecting ? "native_selection" : "closed","completeInWeChat":selecting]
    }
    let list=elements(composer)
    let caption=list.first{string($0,kAXRoleAttribute)==kAXTextAreaRole}
    return ["source":"native","status":"composing","text":caption.map{string($0,kAXValueAttribute)} ?? "",
            "nativeReviewRequired":true,"features":["caption","emoji","add-remove-media","ai","mention","visibility","cancel","post"]]
}
func momentReady(_ root:AXUIElement) throws {
    guard momentComposer(root)==nil else { throw ControlError(code:"moment_existing_draft") }
    // Recover an orphaned media picker left by an interrupted composition.
    if let panel=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) {
        try press(find(panel,["Cancel","取消"]))
        let deadline=Date().addingTimeInterval(5)
        while elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) && Date()<deadline { Thread.sleep(forTimeInterval:0.1) }
    }
    guard !elements(root).contains(where:{string($0,kAXRoleAttribute)==kAXSheetRole}) else { throw ControlError(code:"moment_close_native_dialogs") }
}
func scrollMoment(_ composer:AXUIElement,_ pid:Int32,_ down:Bool) throws {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier==pid,
          let position=attribute(composer,kAXPositionAttribute),let size=attribute(composer,kAXSizeAttribute),
          CFGetTypeID(position)==AXValueGetTypeID(),CFGetTypeID(size)==AXValueGetTypeID() else { throw ControlError(code:"native_control_action_failed") }
    var origin=CGPoint.zero,extent=CGSize.zero
    guard AXValueGetValue(position as! AXValue,.cgPoint,&origin),AXValueGetValue(size as! AXValue,.cgSize,&extent),extent.width>0,extent.height>0 else { throw ControlError(code:"native_control_action_failed") }
    let point=CGPoint(x:origin.x+extent.width*0.8,y:origin.y+extent.height*0.6)
    CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.05)
    CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:down ? -2400 : 2400,wheel2:0,wheel3:0)?.post(tap:.cghidEventTap)
    Thread.sleep(forTimeInterval:0.3)
}
func pickMomentFile(_ root:AXUIElement,_ pid:Int32,_ path:String) throws {
    let deadline=Date().addingTimeInterval(30)
    var panel:AXUIElement?
    repeat {
        panel=elements(root).first{string($0,kAXIdentifierAttribute)=="open-panel"}
        if panel != nil { break };Thread.sleep(forTimeInterval:0.1)
    } while Date()<deadline
    guard panel != nil else { throw ControlError(code:"moment_picker_not_open") }
    try key(pid,5,[.maskCommand,.maskShift])
    let fieldDeadline=Date().addingTimeInterval(5)
    var pathField:AXUIElement?
    repeat {
        pathField=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="PathTextField"})
        if pathField != nil { break };Thread.sleep(forTimeInterval:0.1)
    } while Date()<fieldDeadline
    guard let field=pathField,
          AXUIElementSetAttributeValue(field,kAXValueAttribute as CFString,path as CFString) == .success else { throw ControlError(code:"moment_picker_path_field_missing") }
    // Go to Folder resolves its path asynchronously; Return before the matching
    // suggestion appears can leave the dialog open instead of selecting a file.
    let resolveDeadline=Date().addingTimeInterval(5)
    var resolved=false
    repeat {
        if let go=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="GoToWindow"}) {
            resolved=elements(go).contains{string($0,kAXIdentifierAttribute)==path}
        }
        if resolved { break };Thread.sleep(forTimeInterval:0.1)
    } while Date()<resolveDeadline
    guard resolved else { throw ControlError(code:"moment_picker_path_unresolved") }
    try key(pid,36)
    let navigateDeadline=Date().addingTimeInterval(5)
    while elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="GoToWindow"}) && Date()<navigateDeadline { Thread.sleep(forTimeInterval:0.1) }
    guard !elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="GoToWindow"}) else { throw ControlError(code:"moment_picker_navigation_pending") }
    guard let selectedPanel=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) else { throw ControlError(code:"moment_picker_selection_missing") }
    try press(waitFor(selectedPanel,["Open","打开"]))
    let closeDeadline=Date().addingTimeInterval(5)
    while elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) && Date()<closeDeadline { Thread.sleep(forTimeInterval:0.1) }
    guard !elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) else { throw ControlError(code:"moment_picker_close_pending") }
}
func openMomentTimeline(_ root:AXUIElement,_ pid:Int32) throws -> AXUIElement {
    if let list=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="sns_list"}) { return list }
    guard let discover=try? find(root,["Discover","发现"]) else { throw ControlError(code:"moment_discover_not_found") }
    // WeChat's sidebar exposes Raise as its native activation action.
    // Verify navigation before falling back to a pointer click.
    AXUIElementPerformAction(discover,kAXRaiseAction as CFString)
    AXUIElementPerformAction(discover,kAXPressAction as CFString)
    RunLoop.current.run(until:Date().addingTimeInterval(0.3))
    try press(discover)
    if !momentElements(root).contains(where:{string($0,kAXIdentifierAttribute)=="sns_list"}),
       (try? waitFor(root,["Moments","朋友圈"],1)) == nil {
        AXUIElementPerformAction(discover,kAXPressAction as CFString)
        try press(discover,targeted:true)
        if (try? waitFor(root,["Moments","朋友圈"],1)) == nil {
            if AXUIElementSetAttributeValue(discover,kAXFocusedAttribute as CFString,kCFBooleanTrue) == .success,
               attribute(discover,kAXFocusedAttribute) as? Bool == true { try key(pid,36) }
        }
    }
    if !momentElements(root).contains(where:{string($0,kAXIdentifierAttribute)=="sns_list"}) {
        guard let entry=try? waitFor(root,["Moments","朋友圈"],10) else { throw ControlError(code:"moment_entry_not_found") }
        try press(entry)
    }
    let timelineDeadline=Date().addingTimeInterval(10)
    while !momentElements(root).contains(where:{string($0,kAXIdentifierAttribute)=="sns_list"}) && Date()<timelineDeadline { Thread.sleep(forTimeInterval:0.1) }
    guard momentElements(root).contains(where:{string($0,kAXIdentifierAttribute)=="sns_list"}) else { throw ControlError(code:"moment_timeline_not_found") }
    guard let list=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="sns_list"}) else { throw ControlError(code:"moment_timeline_not_found") }
    return list
}
func momentRows(_ list:AXUIElement) -> [String] {
    let children=attribute(list,kAXChildrenAttribute) as? [AXUIElement] ?? []
    return children.compactMap { item in
        guard string(item,kAXRoleAttribute)==kAXStaticTextRole else { return nil }
        let value=string(item,kAXValueAttribute)
        let text=(value.isEmpty ? label(item) : value).trimmingCharacters(in:.whitespacesAndNewlines)
        guard !text.isEmpty, !["Comments","评论","赞","Like"].contains(text),
              text.range(of:"^余下[0-9]+条$",options:.regularExpression)==nil else { return nil }
        return text
    }
}
func listMoments(_ root:AXUIElement,_ pid:Int32) throws -> [String:Any] {
    guard let limit=request["limit"] as? Int,limit>=1,limit<=1000 else { throw ControlError(code:"invalid_input") }
    guard momentComposer(root)==nil, !elements(root).contains(where:{string($0,kAXRoleAttribute)==kAXSheetRole}) else { throw ControlError(code:"moment_existing_draft") }
    let list=try openMomentTimeline(root,pid)
    let deadline=Date().addingTimeInterval(120)
    // Restore the newest part of the feed before collecting successive viewports.
    var previous:[String]=[]
    for _ in 0..<30 {
        let rows=momentRows(list)
        if !rows.isEmpty && rows==previous { break }
        previous=rows
        try scrollMoment(list,pid,false)
    }
    var items:[[String:Any]]=[], seen=Set<String>(), stalled=0, reason="time_limit"
    while Date()<deadline {
        var added=0
        for text in momentRows(list) {
            let fingerprint=text.replacingOccurrences(of:"[0-9]+ (?:second|minute|hour)\\(s\\) ago|[0-9]+(?:秒|分钟|小时)前",with:"",options:.regularExpression)
            if seen.insert(fingerprint).inserted {
                items.append(["displayText":text]);added+=1
                if items.count>=limit { break }
            }
        }
        if items.count>=limit { reason="limit";break }
        stalled = added==0 ? stalled+1 : 0
        if stalled>=6 { reason="no_more_observed";break }
        try scrollMoment(list,pid,true)
        RunLoop.current.run(until:Date().addingTimeInterval(0.4))
    }
    return ["source":"native","items":items,"total":items.count,"order":"newest_first",
            "scope":"accessible_moments_feed","complete":false,"stopReason":reason,
            "limitations":["WeChat controls feed visibility and history; this is not a complete account archive.","Native post IDs and full media are not exposed by this view."]]
}
func composeMoment(_ root:AXUIElement,_ pid:Int32) throws {
    try momentReady(root)
    guard let directory=request["mediaDirectory"] as? String,
          let count=request["mediaCount"] as? Int,count>=1,count<=9,
          let kind=request["kind"] as? String,["images","video"].contains(kind),
          kind != "video" || count==1,
          let text=request["text"] as? String,text.utf16.count<=8192,!text.contains("\0") else { throw ControlError(code:"invalid_input") }
    let files=try FileManager.default.contentsOfDirectory(atPath:directory).sorted()
    guard files.count==count else { throw ControlError(code:"invalid_input") }
    _ = try openMomentTimeline(root,pid)
    guard let post=try? waitFor(root,["Post","发表"],10) else { throw ControlError(code:"moment_post_button_not_found") }
    AXUIElementPerformAction(post,kAXRaiseAction as CFString)
    let pickerDeadline=Date().addingTimeInterval(3)
    while !elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) && Date()<pickerDeadline { RunLoop.current.run(until:Date().addingTimeInterval(0.1)) }
    if !elements(root).contains(where:{string($0,kAXIdentifierAttribute)=="open-panel"}) { try press(post) }
    for (index,file) in files.enumerated() {
        if index>0 {
            let composer=try waitMoment(root)
            guard let add=elements(composer).first(where:{["Add Image","添加图片"].contains(string($0,kAXValueAttribute)) || ["Add Image","添加图片"].contains(string($0,kAXTitleAttribute))}) else { throw ControlError(code:"native_control_not_found") }
            try press(add)
        }
        try pickMomentFile(root,pid,directory+"/"+file)
        _ = try waitMoment(root)
    }
    let composer=try waitMoment(root)
    if kind=="images" {
        let deadline=Date().addingTimeInterval(5)
        var loaded=0
        repeat {
            loaded=elements(composer).filter{["Image","图片"].contains(string($0,kAXValueAttribute)) || ["Image","图片"].contains(string($0,kAXTitleAttribute))}.count
            if loaded==count { break };Thread.sleep(forTimeInterval:0.1)
        } while Date()<deadline
        guard loaded==count else { throw ControlError(code:"moment_media_count_mismatch") }
    }
    guard let caption=elements(composer).first(where:{string($0,kAXRoleAttribute)==kAXTextAreaRole}),
          string(caption,kAXValueAttribute).isEmpty else { throw ControlError(code:"moment_existing_draft") }
    if !text.isEmpty {
        guard AXUIElementSetAttributeValue(caption,kAXValueAttribute as CFString,text as CFString) == .success,
              string(caption,kAXValueAttribute)==text else { throw ControlError(code:"moment_caption_failed") }
    }
}

do {
    let requestedAction=request["action"] as? String ?? "status"
    if requestedAction=="moments.inspect" {
        guard let path=request["filePath"] as? String,path.hasPrefix("/") else { throw ControlError(code:"invalid_input") }
        let asset=AVURLAsset(url:URL(fileURLWithPath:path))
        let seconds=CMTimeGetSeconds(asset.duration)
        guard seconds.isFinite,seconds>0,seconds<=30,!asset.tracks(withMediaType:.video).isEmpty,asset.isPlayable else { throw ControlError(code:"moment_invalid_video") }
        emit(["durationSeconds":seconds]);exit(0)
    }
    guard AXIsProcessTrusted() else { throw ControlError(code:"native_accessibility_required") }
    guard let pid = request["pid"] as? Int32, let expected = request["app"] as? String else { throw ControlError(code:"native_process_identity_mismatch") }
    // Gadget resumes before LaunchServices necessarily registers the GUI app.
    let registrationDeadline=Date().addingTimeInterval(5)
    var running=NSRunningApplication(processIdentifier:pid)
    while running == nil && Date()<registrationDeadline {
        Thread.sleep(forTimeInterval:0.1)
        running=NSRunningApplication(processIdentifier:pid)
    }
    guard let app=running else { throw ControlError(code:"native_process_identity_mismatch") }
    // LaunchServices can report another copy's bundle URL when both share WeChat's bundle ID.
    // Verify the actual process executable instead, resolving /tmp aliases on both sides.
    var pathBuffer=[CChar](repeating:0,count:4096)
    let pathLength=proc_pidpath(pid,&pathBuffer,UInt32(pathBuffer.count))
    let actualPath=String(cString:pathBuffer)
    let expectedPath=URL(fileURLWithPath:expected).appendingPathComponent("Contents/MacOS/WeChat").resolvingSymlinksInPath().path
    guard pathLength>0, URL(fileURLWithPath:actualPath).resolvingSymlinksInPath().path == expectedPath else { throw ControlError(code:"native_process_identity_mismatch") }
    let root = AXUIElementCreateApplication(pid)
    let action = request["action"] as? String ?? "status"
    if !["status","attachment.status","moments.status"].contains(action) {
        _ = NSApplication.shared
        if let window=attribute(root,kAXMainWindowAttribute) { AXUIElementPerformAction(window as! AXUIElement,kAXRaiseAction as CFString) }
        app.unhide()
        app.activate(options:[.activateIgnoringOtherApps,.activateAllWindows])
        for window in attribute(root,kAXWindowsAttribute) as? [AXUIElement] ?? [] {
            if string(window,kAXTitleAttribute)=="WeChat" {
                AXUIElementSetAttributeValue(window,kAXMinimizedAttribute as CFString,kCFBooleanFalse)
                AXUIElementPerformAction(window,kAXRaiseAction as CFString)
            }
        }
        RunLoop.current.run(until:Date().addingTimeInterval(0.3))
        let deadline=Date().addingTimeInterval(3)
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid && Date()<deadline { RunLoop.current.run(until:Date().addingTimeInterval(0.1)) }
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { throw ControlError(code:"native_app_not_frontmost") }
    }
    if action != "channels.open", let chatName = request["chatName"] as? String, !chatName.isEmpty {
        guard state(root)["chatName"] as? String == chatName else { throw ControlError(code:"native_active_chat_mismatch") }
    }
    if action == "dictation.start" || action == "recording.start" {
        guard let composer=elements(root).first(where:{string($0,kAXIdentifierAttribute)=="chat_input_field"}) else { throw ControlError(code:"native_control_not_found") }
        try press(composer)
        AXUIElementSetAttributeValue(composer,kAXFocusedAttribute as CFString,kCFBooleanTrue)
    }
    switch action {
    case "channels.open":
        try openChannels(root,pid);exit(0)
    case "moments.list":
        emit(try listMoments(root,pid));exit(0)
    case "login.saved":
        let deadline=Date().addingTimeInterval(10)
        repeat {
            let list=elements(root)
            if list.contains(where:{string($0,kAXIdentifierAttribute)=="current_login_nick_name"}),
               let open=try? find(root,["Open WeChat","Log In","登录","进入微信","打开微信"]) {
                try press(open)
                emit(["loginStarted":true]);exit(0)
            }
            if (try? find(root,["Discover","发现"])) != nil { break }
            Thread.sleep(forTimeInterval:0.2)
        } while Date()<deadline
        emit(["loginStarted":false]);exit(0)
    case "moments.ready":
        try momentReady(root);emit(["ready":true]);exit(0)
    case "moments.compose":
        try composeMoment(root,pid);emit(momentState(root));exit(0)
    case "moments.status":
        emit(momentState(root));exit(0)
    case "moments.cancel", "moments.post", "moments.emoji", "moments.mention", "moments.visibility", "moments.ai", "moments.add-media":
        guard let composer=momentComposer(root) else { throw ControlError(code:"moment_composer_not_found") }
        // Raise the existing draft rather than treating lost focus as a closed draft.
        if let window=elements(root).first(where:{string($0,kAXRoleAttribute)==kAXWindowRole && elements($0).contains(where:{CFEqual($0,composer)})}) {
            AXUIElementPerformAction(window,kAXRaiseAction as CFString)
            Thread.sleep(forTimeInterval:0.2)
        }
        // A selector or assistant must be closed before a final action.
        guard momentElements(root).contains(where:{CFEqual($0,composer)}) else { throw ControlError(code:"moment_finish_native_selection") }
        guard let focused=attribute(root,kAXFocusedWindowAttribute),
              elements(focused as! AXUIElement).contains(where:{CFEqual($0,composer)}) || CFEqual(focused,composer) else { throw ControlError(code:"moment_finish_native_selection") }
        if ["moments.ai","moments.mention","moments.visibility"].contains(action) { try scrollMoment(composer,pid,true) }
        if ["moments.emoji","moments.add-media"].contains(action) { try scrollMoment(composer,pid,false) }
        let names:[String]
        switch action {
        case "moments.cancel": names=["Cancel","取消"]
        case "moments.post": names=["Post","发表"]
        case "moments.emoji": names=["Send Sticker","发送表情"]
        case "moments.visibility": names=["Visible To","谁可以看"]
        case "moments.ai": names=["AI 帮写"]
        case "moments.mention": names=["Mention","提醒谁看"]
        default: names=["Add Image","添加图片"]
        }
        if action=="moments.mention" || action=="moments.add-media" {
            guard let target=elements(composer).first(where:{ names.contains(string($0,kAXValueAttribute)) || names.contains(string($0,kAXTitleAttribute)) }) else { throw ControlError(code:"native_control_not_found") }
            try press(target)
        } else {
            // Visible To includes the selected audience in its accessible label.
            if action=="moments.visibility" {
                guard let target=elements(composer).first(where:{e in string(e,kAXRoleAttribute)==kAXButtonRole && names.contains(where:{name in label(e).hasPrefix(name)})}) else { throw ControlError(code:"native_control_not_found") }
                try press(target)
            } else {
                let button=try find(composer,names)
                if action=="moments.post" { fileSendStarted=true }
                try press(button)
            }
        }
        if action=="moments.post" || action=="moments.cancel" {
            if action=="moments.cancel" {
                Thread.sleep(forTimeInterval:0.2)
                if let prompt=elements(root).first(where:{e in string(e,kAXRoleAttribute)==kAXSheetRole && elements(e).contains(where:{string($0,kAXValueAttribute)=="Save this draft?"})}) {
                    try press(find(prompt,["Discard"]))
                }
            }
            let deadline=Date().addingTimeInterval(5)
            while momentComposer(root) != nil && Date()<deadline { Thread.sleep(forTimeInterval:0.1) }
            guard momentComposer(root)==nil else { throw ControlError(code:action=="moments.post" ? "moment_post_outcome_unknown" : "moment_cancel_pending") }
            emit(["source":"native","status":action=="moments.post" ? "submitted" : "cancelled","submittedToWeChat":action=="moments.post","acknowledgedByServer":false]);exit(0)
        }
        emit(["source":"native","status":"native_selection","feature":action,"completeInWeChat":true]);exit(0)
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
    if !["status","attachment.status","moments.status","moments.ready"].contains(action) { Thread.sleep(forTimeInterval:0.15) }
    emit(state(root))
} catch let error as ControlError { emit(["error":error.code,"possibleSend":fileSendStarted]) }
catch { emit(["error":"native_control_failed"]) }
