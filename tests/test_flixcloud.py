import httpx
import pytest

from app.resolvers.flixcloud import FlixCloudResolveError, FlixCloudResolver


def test_validate_embed_url_extracts_id():
    assert FlixCloudResolver.validate_embed_url(
        "https://flixcloud.cc/e/abc12345?v=2"
    )[0] == "abc12345"


@pytest.mark.parametrize("url", [
    "http://flixcloud.cc/e/abc12345",
    "https://evil.example/e/abc12345",
    "https://flixcloud.cc/api/m3u8/abc12345",
])
def test_rejects_untrusted_or_invalid_embed_urls(url):
    with pytest.raises(FlixCloudResolveError):
        FlixCloudResolver.validate_embed_url(url)


@pytest.mark.asyncio
async def test_resolve_extracts_video_id_and_fetches_metadata_without_exposing_opaque_fields():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/e/abc12345":
            return httpx.Response(
                200,
                text='<script>const player = {"video_id":"video987654321"};</script>',
            )
        if request.url.path == "/api/m3u8/video987654321":
            return httpx.Response(200, json={
                "version": "2.1",
                "duration": 1420,
                "obfuscated_crypto_data": "do-not-return",
                "token": "secret-token",
            })
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    resolver = FlixCloudResolver(client)
    result = await resolver.resolve("https://flixcloud.cc/e/abc12345?v=2")

    assert result["embed_id"] == "abc12345"
    assert result["video_id"] == "video987654321"
    assert result["status"] == "metadata_received"
    assert result["playback_ready"] is False
    assert result["metadata"] == {"version": "2.1", "duration": 1420}
    assert calls[1].headers["referer"] == "https://flixcloud.cc/e/abc12345?v=2"
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_reports_provider_access_denial_without_retrying():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403)),
        follow_redirects=False,
    )
    resolver = FlixCloudResolver(client)
    with pytest.raises(FlixCloudResolveError) as error:
        await resolver.resolve("https://flixcloud.cc/e/abc12345")
    assert error.value.status_code == 403
    await client.aclose()
