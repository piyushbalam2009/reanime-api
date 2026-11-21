import json

import httpx
import pytest

from app.resolvers.flixcloud import FlixCloudResolveError, FlixCloudResolver


def test_validate_embed_url_extracts_id():
    assert FlixCloudResolver.validate_embed_url("https://flixcloud.cc/e/abc12345?v=2")[0] == "abc12345"


@pytest.mark.parametrize("url", [
    "http://flixcloud.cc/e/abc12345",
    "https://evil.example/e/abc12345",
    "https://flixcloud.cc/api/m3u8/abc12345",
])
def test_rejects_untrusted_or_invalid_embed_urls(url):
    with pytest.raises(FlixCloudResolveError):
        FlixCloudResolver.validate_embed_url(url)


def test_extract_video_id_accepts_unquoted_key():
    html = '<script>const player = {video_id:"f4762abb-b524-4903-8dc1-68f82367e70b"};</script>'
    assert FlixCloudResolver._extract_video_id(html) == "f4762abb-b524-4903-8dc1-68f82367e70b"


def test_safe_subtitles_filters_bad_urls():
    tracks = [
        {"url": "https://subs.flixcloud.cc/en.vtt", "language": "English", "format": "vtt", "default": True},
        {"url": "javascript:alert(1)"},
        {"url": "http://subs.example/sub.srt"},
    ]
    assert FlixCloudResolver._safe_subtitles(tracks) == [
        {"url": "https://subs.flixcloud.cc/en.vtt", "language": "English", "format": "vtt", "default": True}
    ]


def _flaresolverr_mock(html):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        payload = json.loads(request.content)
        cmd = payload["cmd"]
        if cmd in ("sessions.create", "sessions.destroy"):
            return httpx.Response(200, json={"status": "ok", "message": "ok"})
        if cmd == "request.get":
            return httpx.Response(200, json={
                "status": "ok",
                "solution": {
                    "status": 200, "response": "<html>origin ready</html>",
                    "cookies": [{"name": "cf_clearance", "value": "clear", "domain": ".flixcloud.cc"}],
                    "userAgent": "test",
                },
            })
        return httpx.Response(400, json={"status": "error", "message": "unsupported"})
    return handler


@pytest.mark.asyncio
async def test_resolve_uses_one_browser_session_and_returns_playable_url(monkeypatch):
    html = '<script>const player = {video_id:"video987654321"};</script>'
    client = httpx.AsyncClient(transport=httpx.MockTransport(_flaresolverr_mock(html)))
    resolver = FlixCloudResolver(client)
    calls = []

    async def fake_decryptor(page_html, embed_url, session_id):
        calls.append((embed_url, session_id))
        return {
            "url": "https://fetch1.flixcloud.cc/_v8/example/master.m3u8?token=abc",
            "video_id": "video987654321",
            "subtitles": [{"url": "https://subs.flixcloud.cc/en.vtt", "language": "English"}],
            "thumbnails_vtt": "https://fetch1.flixcloud.cc/thumbnails_vtt/example",
            "video_title": "Episode 1",
            "intro_chapter": None,
            "outro_chapter": None,
        }

    monkeypatch.setattr(resolver, "_run_decryptor", fake_decryptor)
    result = await resolver.resolve("https://flixcloud.cc/e/abc12345?v=2")
    assert result["playback_ready"] is True
    assert result["url"].startswith("https://fetch1.flixcloud.cc/")
    assert result["video_id"] == "video987654321"
    assert result["subtitles"][0]["language"] == "English"
    assert calls[0][0] == "https://flixcloud.cc/e/abc12345?v=2"
    assert calls[0][1].startswith("reanime-")
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_rejects_external_stream_domain(monkeypatch):
    html = "<html>embed</html>"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_flaresolverr_mock(html)))
    resolver = FlixCloudResolver(client)

    async def fake_decryptor(page_html, embed_url, session_id):
        return {"url": "https://attacker.example/master.m3u8"}

    monkeypatch.setattr(resolver, "_run_decryptor", fake_decryptor)
    with pytest.raises(FlixCloudResolveError, match="outside its provider domain"):
        await resolver.resolve("https://flixcloud.cc/e/abc12345")
    await client.aclose()


@pytest.mark.asyncio
async def test_flaresolverr_challenge_failure_is_reported():
    challenge = '<html><title>Just a moment...</title><script src="https://challenges.cloudflare.com/x"></script></html>'
    client = httpx.AsyncClient(transport=httpx.MockTransport(_flaresolverr_mock(challenge)))
    resolver = FlixCloudResolver(client)
    with pytest.raises(FlixCloudResolveError, match="could not complete"):
        await resolver._flaresolverr_get("https://flixcloud.cc/e/abc12345", "reanime-" + "a" * 32)
    await client.aclose()


@pytest.mark.asyncio
async def test_decryptor_maps_http_410_without_retry(monkeypatch):
    resolver = FlixCloudResolver()

    class FakeProcess:
        returncode = 1
        async def communicate(self, data):
            return b"", b"FlixCloud token metadata returned HTTP 410"

    async def fake_process(*args, **kwargs):
        return FakeProcess()

    import app.resolvers.flixcloud as module
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/node")
    monkeypatch.setattr(module.Path, "is_file", lambda _: True)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fake_process)
    with pytest.raises(FlixCloudResolveError) as exc:
        await resolver._run_decryptor(
            "<html/>", "https://flixcloud.cc/e/abc12345", "reanime-" + "a" * 32
        )
    assert exc.value.status_code == 410
