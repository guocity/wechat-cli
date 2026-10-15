"""Decode bounded WeChat XML metadata and keep native cache keys out of API responses."""

import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

TYPES = {
    1: ("text", "Text"),
    3: ("image", "Image"),
    34: ("voice", "Voice message"),
    37: ("friend_request", "Friend request"),
    40: ("friend_recommendation", "Friend recommendation"),
    42: ("contact_card", "Contact card"),
    43: ("video", "Video"),
    47: ("sticker", "Sticker"),
    48: ("location", "Location"),
    49: ("app", "App message"),
    50: ("call", "Voice/video call"),
    51: ("status", "Status update"),
    52: ("call_notification", "Call notification"),
    53: ("call_invitation", "Call invitation"),
    62: ("video", "Short video"),
    9999: ("system", "System notice"),
    10000: ("system", "System message"),
    10002: ("recalled", "Recalled message"),
}
APP_TYPES = {
    1: ("text", "Text"),
    2: ("image", "Image"),
    3: ("music", "Music"),
    4: ("video", "Video"),
    5: ("link", "Link"),
    6: ("file", "File"),
    7: ("app", "App attachment"),
    8: ("sticker", "Sticker"),
    19: ("chat_history", "Forwarded chat history"),
    24: ("note", "Note"),
    33: ("mini_program", "Mini program"),
    36: ("mini_program", "Mini program"),
    40: ("group_invitation", "Group invitation"),
    50: ("channels_live", "Channels live stream"),
    51: ("channels_video", "Channels video"),
    57: ("quote", "Quoted reply"),
    62: ("pat", "Pat"),
    63: ("channels_live", "Channels live stream"),
    2000: ("transfer", "Transfer"),
    2001: ("red_packet", "Red packet"),
}
FOLDERS = {
    "@placeholder_foldgroup": ("minimized_groups", "Minimized groups"),
    "brandsessionholder": ("subscriptions", "Subscriptions"),
    "brandservicesessionholder": ("service_accounts", "Service accounts"),
}


class Message(dict):
    def __init__(self, value):
        super().__init__(value)
        self.native_media = self.pop("nativeMediaFields", {}) or {}


def xml(content):
    if (
        not isinstance(content, str)
        or len(content) > 1048576
        or re.search(r"<!DOCTYPE|<!ENTITY", content, re.I)
    ):
        return None
    try:
        root = ET.fromstring(content[content.index("<") :])
        pending, nodes = [(root, 0)], 0
        while pending:
            node, depth = pending.pop()
            nodes += 1
            if nodes > 12000 or depth > 48:
                return None
            pending.extend((child, depth + 1) for child in node)
        return root
    except (ET.ParseError, ValueError, RecursionError):
        return None


def find(root, name):
    return next(root.iter(name), None) if root is not None else None


def text(node):
    return "".join(node.itertext()).strip() if node is not None else ""


def value(node, path):
    return text(node.find(path)) if node is not None else ""


def web_url(raw):
    try:
        return raw if urlsplit(raw).scheme in {"http", "https"} and urlsplit(raw).hostname else None
    except ValueError:
        return None


def resolve_chat(chat):
    chat = dict(chat)
    identifier = chat["id"]
    if identifier in FOLDERS:
        kind, name = FOLDERS[identifier]
        chat.update(
            displayName=name,
            chatType="folder",
            folderKind=kind,
            isGroup=False,
            folderMembershipKnown=kind == "minimized_groups",
        )
    else:
        group = identifier.endswith("@chatroom")
        kind = (
            "group"
            if group
            else "file_transfer"
            if identifier == "filehelper"
            else "official_account"
            if identifier.startswith(("gh_", "biz_"))
            else "private"
        )
        chat.update(
            chatType=kind,
            isGroup=group,
            parentFolderId="@placeholder_foldgroup" if group and chat.get("isMinimized") else None,
        )
    return chat


def resolve_message(raw, depth=0):
    message = Message(raw)
    code = int(message.get("type") or 0)
    kind, label = TYPES.get(code, ("unknown", f"Unknown message ({code})"))
    content = message.get("content") or ""
    message.update(kind=kind, typeLabel=label, summary=content or label, details={})
    tree = xml(content) if code != 1 else None
    msg = find(tree, "msg")
    details = message["details"]
    if code == 49:
        app = msg.find("appmsg") if msg is not None else None
        if app is None:
            app = find(tree, "appmsg")
        subtype = value(app, "type")
        subtype = int(subtype) if subtype.isdigit() else None
        kind, label = APP_TYPES.get(subtype, ("app", f"App message ({subtype})"))
        message.update(kind=kind, typeLabel=label, appType=subtype)
        details.update(title=value(app, "title"), description=value(app, "des"))
        url = web_url(value(app, "url"))
        if url:
            details["url"] = url
        if kind == "file":
            details.update(fileName=details["title"], extension=value(app, "appattach/fileext"))
            size = value(app, "appattach/totallen")
            if size.isdigit():
                details["sizeBytes"] = int(size)
            md5 = value(app, "appattach/filemd5") or value(app, "appattach/md5")
            if re.fullmatch(r"[a-fA-F0-9]{32}", md5):
                details["md5"] = md5.lower()
        if kind == "quote":
            source = value(app, "refermsg/content")
            quoted_type = value(app, "refermsg/type")
            quote = {
                "sender": value(app, "refermsg/displayname"),
                "content": source,
                "type": int(quoted_type) if quoted_type.isdigit() else 0,
            }
            if depth < 4:
                for _ in range(3):
                    source = html.unescape(source)
                decoded = resolve_message(
                    {
                        "type": 49 if find(xml(source), "appmsg") is not None else quote["type"],
                        "content": source,
                    },
                    depth + 1,
                )
                quote.update(
                    content=decoded["summary"],
                    kind=decoded["kind"],
                    typeLabel=decoded["typeLabel"],
                    details=decoded["details"],
                )
            details["quote"] = quote
        if kind in {"chat_history", "note"}:
            record = find(app, "recorditem")
            record = record if record is not None and len(record) else xml(text(record))
            listing = find(record, "datalist")
            details["items"] = [
                {
                    "sender": value(n, "sourcename"),
                    "content": value(n, "datadesc") or value(n, "datatitle") or "Attachment",
                    "dataType": n.get("datatype"),
                }
                for n in list(listing if listing is not None else [])[:100]
            ]
        message["summary"] = details["title"] or details["description"] or label
    elif code in {3, 34, 43, 47, 62}:
        tag = {3: "img", 34: "voicemsg", 47: "emoji"}.get(code, "videomsg")
        node = msg.find(tag) if msg is not None else None
        attrs = node.attrib if node is not None else {}
        media = {}
        md5 = attrs.get("md5") or attrs.get("voicemd5")
        if md5 and re.fullmatch(r"[a-fA-F0-9]{32}", md5):
            media["md5"] = md5.lower()
        cache_key = message.native_media.get({3: "392", 34: "voiceFile"}.get(code, "416"))
        if isinstance(cache_key, str) and re.fullmatch(r"[\w.-]{8,128}", cache_key) and ".." not in cache_key:
            media["cacheKey"] = cache_key
        details.update(mediaAvailable=None, media=media)
        duration = attrs.get("voicelength" if code == 34 else "playlength", "")
        if duration.isdigit() and int(duration) > 0:
            details["durationMs"] = int(duration) * (1 if code == 34 else 1000)
        if code == 34 and message.native_media.get("264"):
            details["transcript"] = message.native_media["264"]
        message["summary"] = attrs.get("desc") or attrs.get("attachedtext") or label
    elif code == 48:
        node = find(tree, "location")
        attrs = node.attrib if node is not None else {}
        details.update(
            label=attrs.get("label", ""),
            name=attrs.get("poiname", ""),
            latitude=attrs.get("x"),
            longitude=attrs.get("y"),
        )
        message["summary"] = details["name"] or details["label"] or label
    elif code == 42:
        attrs = msg.attrib if msg is not None else {}
        details.update(name=attrs.get("nickname", ""), userId=attrs.get("username", ""))
        message["summary"] = details["name"] or label
    elif code in {9999, 10000, 10002}:
        message["summary"] = (
            text(find(tree, "replacemsg")) or text(find(tree, "plain")) or text(msg) or content or label
        )
    elif tree is not None:
        message["summary"] = text(find(tree, "title")) or text(find(tree, "wording")) or label
    message["displaySummary"] = message["summary"]
    return message


def media_descriptor(message):
    tree = xml(message.get("content", ""))
    tag = {3: "img", 34: "voicemsg", 43: "videomsg", 47: "emoji", 49: "emoji", 62: "videomsg"}.get(
        int(message["type"])
    )
    node = find(tree, tag) if tag else None
    attrs = node.attrib if node is not None else {}
    native = getattr(message, "native_media", {})
    media = message["details"].get("media", {})
    return {
        "cacheKeys": [native.get(k) for k in ["serverHash", "serverHashMiddle", "120"] if native.get(k)],
        "md5": message["details"].get("md5") or media.get("cacheKey") or media.get("md5"),
        "key": attrs.get("aeskey"),
        "thumbnailKey": attrs.get("cdnthumbaeskey"),
        "url": web_url(attrs.get("cdnurl", "")) if message["kind"] == "sticker" else None,
        "encryptedUrl": web_url(attrs.get("encrypturl", "")) if message["kind"] == "sticker" else None,
        "thumbnailUrl": web_url(attrs.get("thumburl", "")) if message["kind"] == "sticker" else None,
    }
