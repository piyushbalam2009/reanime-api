from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.hls_proxy import rewrite_playlist, validate_upstream_url


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
