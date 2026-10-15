import json

from wechat_cli.models import media_descriptor, resolve_chat, resolve_message, xml


def test_xml_is_bounded_and_rejects_entities():
    assert xml('<!DOCTYPE x [<!ENTITY e "text">]><msg>&e;</msg>') is None
    assert xml("<msg>" + "x" * 1048576 + "</msg>") is None
    assert xml("<x>" * 60 + "</x>" * 60) is None
    assert xml("<msg><broken></msg>") is None


def test_message_metadata_does_not_use_quoted_subtype():
    content = "<msg><appmsg><title>report.pdf</title><type>6</type><appattach><totallen>123</totallen><fileext>pdf</fileext></appattach><refermsg><type>57</type></refermsg></appmsg></msg>"
    result = resolve_message({"id": "18446744073709551615", "type": 49, "content": content})
    assert result["kind"] == "file" and result["appType"] == 6
    assert result["details"]["sizeBytes"] == 123
    assert result["details"]["fileName"] == "report.pdf"
    assert result["id"] == "18446744073709551615"


def test_native_media_is_retained_internally_but_not_serialized():
    message = resolve_message(
        {
            "type": 3,
            "content": '<msg><img aeskey="SECRET" md5="' + "a" * 32 + '"/></msg>',
            "nativeMediaFields": {"serverHash": "private-cache-key"},
        }
    )
    assert media_descriptor(message)["key"] == "SECRET"
    assert media_descriptor(message)["cacheKeys"] == ["private-cache-key"]
    assert "private-cache-key" not in json.dumps(message)
    assert "nativeMediaFields" not in message


def test_nested_quote_is_readable():
    content = "<msg><appmsg><type>57</type><title>Reply</title><refermsg><type>49</type><displayname>Sender</displayname><content>&lt;msg&gt;&lt;appmsg&gt;&lt;type&gt;6&lt;/type&gt;&lt;title&gt;file.txt&lt;/title&gt;&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>"
    message = resolve_message({"type": 49, "content": content})
    assert message["details"]["quote"]["content"] == "file.txt"
    assert message["details"]["quote"]["kind"] == "file"


def test_chat_types_and_folder_membership():
    assert (
        resolve_chat({"id": "123@chatroom", "isMinimized": True})["parentFolderId"]
        == "@placeholder_foldgroup"
    )
    assert resolve_chat({"id": "gh_123"})["chatType"] == "official_account"
    assert resolve_chat({"id": "@placeholder_foldgroup"})["folderMembershipKnown"] is True
