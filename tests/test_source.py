import json

import httpx
import pytest

from app.source import ReAnimeAdapter, SourceRequestError


@pytest.mark.asyncio
async def test_search_uses_public_catalog_api_and_normalizes_ids():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [
            {"anime_id": "solo-leveling-abc", "title": {"english": "Solo Leveling"}}
        ]})

    client = httpx.AsyncClient(
        base_url="https://reanime.cz",
        transport=httpx.MockTransport(handler),
    )
    adapter = ReAnimeAdapter(client)
    result = await adapter.search("Solo Leveling", 2)
    assert result[0]["id"] == "solo-leveling-abc"
    assert calls[0].url.path == "/api/v1/search"
    assert calls[0].url.params["offset"] == "20"
    await client.aclose()


@pytest.mark.asyncio
async def test_episode_ids_and_sorting():
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"episode_number": 2, "title": "Two"},
            {"episode_number": 1, "title": "One"},
        ]})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    episodes = await adapter.episodes("sample-anime")
    assert [x["id"] for x in episodes] == ["sample-anime:1", "sample-anime:2"]
    await client.aclose()


@pytest.mark.asyncio
async def test_episode_preserves_upstream_episode_id():
    def handler(request):
        return httpx.Response(200, json={"episodes": [
            {"episodeId": "ep-1", "episode_number": 1, "playable": True}
        ]})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    episodes = await adapter.episodes("sample-anime")
    assert episodes[0]["id"] == "sample-anime:1"
    assert episodes[0]["source_episode_id"] == "ep-1"
    await client.aclose()


@pytest.mark.asyncio
async def test_servers_falls_back_to_versioned_watch_route():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/watch/demo/1":
            return httpx.Response(404, json={})
        if request.url.path == "/api/v1/watch/demo/1":
            return httpx.Response(200, json={"anime": {}, "episode_links": [
                {"serverName": "HD-1", "dataType": "sub", "dataLink": "https://embed.example/sub"}
            ]})
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    result = await adapter.servers("demo:1")
    assert result["sub"][0]["dataLink"] == "https://embed.example/sub"
    assert calls == ["/api/watch/demo/1", "/api/v1/watch/demo/1"]
    await client.aclose()


@pytest.mark.asyncio
async def test_servers_separate_sub_and_dub():
    def handler(request):
        if request.url.path == "/api/watch/demo/1":
            return httpx.Response(200, json={"anime": {}, "episode_links": [
                {"serverName": "HD-1", "dataType": "sub", "dataLink": "https://embed.example/sub"},
                {"serverName": "HD-2", "dataType": "dub", "dataLink": "https://embed.example/dub"},
            ]})
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    result = await adapter.servers("demo:1")
    assert len(result["sub"]) == 1
    assert len(result["dub"]) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_playback_returns_embed_without_faking_hls():
    def handler(request):
        return httpx.Response(200, json={"anime": {}, "episode_links": [
            {"serverName": "HD-2", "dataType": "sub", "dataLink": "https://embed.example/watch/123"}
        ]})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    result = await adapter.playback("demo:1", "sub")
    assert result["embed_url"] == "https://embed.example/watch/123"
    assert "master_m3u8" not in result
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_episode_id_is_rejected():
    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    adapter = ReAnimeAdapter(client)
    with pytest.raises(SourceRequestError) as error:
        await adapter.servers("https://attacker.example/path")
    assert error.value.status_code == 400
    await client.aclose()
