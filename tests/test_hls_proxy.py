from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.hls_proxy import (
    _decode_playlist,
    _payload_kind,
    _playlist_key_for_url,
    store_playlist_key,
    proxy_url_for,
    rewrite_playlist,
    validate_upstream_url,
)


def _request():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/api/proxy/hls",
        "raw_path": b"/api/proxy/hls",
        "query_string": b"",
        "headers": [(b"host", b"api.example.test")],
        "server": ("api.example.test", 443),
        "client": ("127.0.0.1", 1234),
    }
    return Request(scope)


def test_validate_allows_flixcloud_hls_url():
    assert validate_upstream_url("https://fetch8.flixcloud.cc/master.m3u8").startswith("https://")


@pytest.mark.parametrize("url", [
    "http://fetch8.flixcloud.cc/master.m3u8",
    "https://evil.example/master.m3u8",
    "https://127.0.0.1/master.m3u8",
    "https://flixcloud.cc.evil.example/master.m3u8",
    "https://user:pass@flixcloud.cc/master.m3u8",
])
def test_validate_rejects_unsafe_upstream_urls(url):
    with pytest.raises(HTTPException):
        validate_upstream_url(url)


def test_rewrite_playlist_rewrites_relative_segments_and_uri_attributes():
    base = "https://fetch8.flixcloud.cc/path/master.m3u8"
    body = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin"\nvariant/index.m3u8\nsegment001.ts\n'
    rewritten = rewrite_playlist(body, base, _request())
    urls = []
    for line in rewritten.splitlines():
        if 'URI="' in line:
            urls.append(line.split('URI="', 1)[1].split('"', 1)[0])
        elif line and not line.startswith("#"):
            urls.append(line)
    assert len(urls) == 3
    for value in urls:
        parsed = urlsplit(value)
        assert parsed.path == "/api/proxy/hls"
        assert parse_qs(parsed.query)["url"][0].startswith("https://fetch8.flixcloud.cc/path/")


def test_proxy_url_for_builds_api_url_for_hls_and_subtitle_resources():
    target = "https://fetch8.flixcloud.cc/master.m3u8?token=abc"
    value = proxy_url_for(_request(), target)
    parsed = urlsplit(value)
    assert parsed.path == "/api/proxy/hls"
    assert parse_qs(parsed.query)["url"] == [target]


def test_decode_playlist_accepts_base64_wrapped_plain_hls():
    import base64

    manifest = b"#EXTM3U\n#EXTINF:5,\nsegment.ts\n"
    wrapped = base64.b64encode(manifest)
    assert _decode_playlist(wrapped, "application/vnd.apple.mpegurl") == manifest.decode()


def test_decode_playlist_does_not_treat_base64_opaque_data_as_hls():
    import base64

    opaque = base64.b64encode(b"not a playlist")
    with pytest.raises(HTTPException) as raised:
        _decode_playlist(opaque, "application/vnd.apple.mpegurl")
    assert raised.value.detail["error"] == "upstream_not_hls"


def test_decode_playlist_decodes_flixcloud_base64_xor_envelope():
    import base64

    key = bytes(range(32))
    manifest = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nvideo-480p.m3u8\n"
    encrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(manifest))
    payload = base64.b64encode(encrypted)
    assert _decode_playlist(payload, "application/vnd.apple.mpegurl", key) == manifest.decode()


def test_playlist_key_is_stored_server_side_and_matched_to_video_path():
    import base64

    video_id = "f4762abb-b524-4903-8dc1-68f82367e70b"
    key = bytes(range(32))
    assert store_playlist_key(video_id, base64.b64encode(key).decode()) is True
    target = f"https://fetch9.flixcloud.cc/_v7/{video_id}/video-480p.m3u8"
    assert _playlist_key_for_url(target) == key
    assert _playlist_key_for_url("https://fetch9.flixcloud.cc/other/master.m3u8") is None


def test_playlist_key_rejects_invalid_length():
    import base64

    assert store_playlist_key("f4762abb-b524-4903-8dc1-68f82367e70b", base64.b64encode(b"short").decode()) is False


def test_decode_playlist_accepts_valid_hls_and_utf8_bom():
    assert _decode_playlist(b"\xef\xbb\xbf #EXTM3U\n#EXTINF:5,\nsegment.ts\n", "application/octet-stream").startswith(" #EXTM3U")


@pytest.mark.parametrize(
    ("payload", "content_type", "kind"),
    [
        (b'{"message":"not a manifest"}', "application/vnd.apple.mpegurl", "json"),
        (b"<html><title>challenge</title></html>", "application/vnd.apple.mpegurl", "html"),
        (b"opaque-token-payload", "application/vnd.apple.mpegurl", "text"),
        (b"\x00\xff\x10", "application/vnd.apple.mpegurl", "binary_or_encoded"),
    ],
)
def test_decode_playlist_rejects_non_hls_payload_without_echoing_body(payload, content_type, kind):
    with pytest.raises(HTTPException) as raised:
        _decode_playlist(payload, content_type)
    assert raised.value.status_code == 502
    detail = raised.value.detail
    assert detail["error"] == "upstream_not_hls"
    assert detail["upstream_body_kind"] == kind
    assert detail["upstream_bytes"] == len(payload)
    assert payload.decode("utf-8", errors="ignore") not in str(detail)


def test_payload_kind_identifies_valid_hls():
    assert _payload_kind(b"\xef\xbb\xbf#EXTM3U\n") == "hls"
