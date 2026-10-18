"""Channels management wire contract; no live videos are changed."""

import json

import pytest

from wechat_cli.cli import main, parser
from wechat_cli.core.errors import AppError


def management(native):
    native.capabilities = lambda: {"channelsPosting": "experimental"}
    calls = []
    native.list_channel_videos = lambda **query: (
        calls.append(("videos", query))
        or {"items": [{"id": "18446744073709551615"}], "nextCursor": "next+/=中"}
    )
    native.channel_stats = lambda: calls.append(("stats", None)) or {"followers": 12}
    native.delete_channel_video = lambda video_id: (
        calls.append(("delete", video_id)) or {"videoId": video_id, "deleted": True}
    )
    return calls


def test_channels_reads_and_opaque_cursor(api):
    _, client, native = api
    calls = management(native)
    assert client.list_channel_videos()["items"][0]["id"] == "18446744073709551615"
    assert client.list_channel_videos(3, "next+/=中")["nextCursor"] == "next+/=中"
    assert client.channel_stats() == {"followers": 12}
    assert calls == [
        ("videos", {"limit": 20, "cursor": None}),
        ("videos", {"limit": 3, "cursor": "next+/=中"}),
        ("stats", None),
    ]


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "limit=01", "limit=1.5", "cursor=", "cursor=%00", "extra=x", "limit=2&limit=3"],
)
def test_channels_video_query_validation(api, query):
    _, client, native = api
    calls = management(native)
    with pytest.raises(AppError) as error:
        client.request("/api/channels/videos?" + query)
    assert error.value.status == 400
    assert calls == []


def test_channels_management_requires_supported_backend(api):
    _, client, native = api
    for path in ["/api/channels/videos", "/api/channels/stats"]:
        with pytest.raises(AppError) as error:
            client.request(path)
        assert error.value.code == "native_feature_unavailable"
    assert native.calls == []


def test_channels_delete_requires_key_and_replays_once(api):
    _, client, native = api
    calls = management(native)
    identifier = "18446744073709551615"
    with pytest.raises(AppError) as error:
        client.request("/api/channels/delete", method="POST", data={"videoId": identifier})
    assert error.value.code == "idempotency_key_required"
    result = client.delete_channel_video(identifier, idempotency_key="delete-video-001")
    assert client.delete_channel_video(identifier, idempotency_key="delete-video-001") == result
    with pytest.raises(AppError) as error:
        client.delete_channel_video("different-id", idempotency_key="delete-video-001")
    assert error.value.code == "idempotency_conflict"
    assert calls == [("delete", identifier)]


@pytest.mark.parametrize(
    "body",
    [
        {"videoId": ""},
        {"videoId": " "},
        {"videoId": "x\0y"},
        {"videoId": 123},
        {"videoId": "x", "extra": True},
    ],
)
def test_channels_delete_validation(api, body):
    _, client, native = api
    calls = management(native)
    with pytest.raises(AppError) as error:
        client.request("/api/channels/delete", method="POST", data=body, key="delete-video-002")
    assert error.value.status == 400
    assert calls == []


def test_channels_cli_routes_and_key(api, capsys):
    server, _, native = api
    calls = management(native)
    common = ["--data-dir", str(server.directory), "--port", str(server.server_address[1])]
    assert main([*common, "channels", "videos", "--limit", "3", "--cursor", "next+/=中"]) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["id"] == "18446744073709551615"
    assert main(["channels", "stats", *common]) == 0
    assert json.loads(capsys.readouterr().out) == {"followers": 12}
    assert main(["channels", "delete", "123", "--key", "cli-delete-001", *common]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out) == {"videoId": "123", "deleted": True}
    assert "cli-delete-001" in result.err
    assert calls == [
        ("videos", {"limit": 3, "cursor": "next+/=中"}),
        ("stats", None),
        ("delete", "123"),
    ]
    assert main(["channels", "delete", "123", "--key", "cli-delete-001", *common]) == 0
    capsys.readouterr()
    assert calls.count(("delete", "123")) == 1
    with pytest.raises(SystemExit):
        parser().parse_args(["channels", "videos", "--limit", "101"])


def test_channels_cli_delete_generated_key_can_be_reused(api, capsys):
    server, _, native = api
    calls = management(native)
    common = ["--data-dir", str(server.directory), "--port", str(server.server_address[1])]
    assert main(["channels", "delete", "123", *common]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["deleted"] is True
    key = output.err.split("Idempotency-Key: ", 1)[1].split()[0]
    assert len(key) == 36
    assert main(["channels", "delete", "123", "--key", key, *common]) == 0
    capsys.readouterr()
    assert calls == [("delete", "123")]


@pytest.mark.parametrize(
    "argv",
    [
        ["--port", "8788", "channels", "stats"],
        ["channels", "--port", "8788", "stats"],
        ["channels", "stats", "--port", "8788"],
    ],
)
def test_channels_cli_common_options_at_each_level(argv):
    args = parser().parse_args(argv)
    assert args.port == 8788 and args.channel_command == "stats"


@pytest.mark.parametrize("post", [False, True])
def test_channels_upload_cli_forwards_original_declaration(api, capsys, tmp_path, post):
    server, _, native = api
    native.capabilities = lambda: {"channelsPosting": "experimental"}
    calls = []
    native.compose_channel = lambda payload, **options: (
        calls.append((payload, options)) or {"status": "ready", "settings": {"declareOriginal": True}}
    )
    path = tmp_path / "paris.mp4"
    argv = [
        "channels",
        "upload",
        str(path),
        "--title",
        "Paris",
        "--description",
        "Paris",
        "--declare-original",
        "--key",
        "original-upload-001",
        "--data-dir",
        str(server.directory),
        "--port",
        str(server.server_address[1]),
    ]
    if post:
        argv.append("--post")
    assert main(argv) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["settings"]["declareOriginal"] is True
    assert "original-upload-001" in output.err
    assert len(calls) == 1
    assert calls[0][0]["videoPath"] == str(path.resolve())
    assert calls[0][0]["options"]["declareOriginal"] is True
    assert calls[0][1].get("post", False) is post
    assert main(argv) == 0
    capsys.readouterr()
    assert len(calls) == 1


def test_originality_terms_need_an_explicit_original_declaration():
    from pydantic import ValidationError

    from wechat_cli.api.schemas import ChannelOptions

    with pytest.raises(ValidationError):
        ChannelOptions(acceptOriginalityTerms=True)
    assert ChannelOptions(declareOriginal=True, acceptOriginalityTerms=True).acceptOriginalityTerms is True


def test_upload_cli_exposes_explicit_originality_terms_flag():
    args = parser().parse_args(
        ["channels", "upload", "/tmp/paris.mp4", "--declare-original", "--accept-originality-terms"]
    )
    assert args.declare_original is True
    assert args.accept_originality_terms is True
