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
            {"episodeId": "ep-2", "episode_number": 2, "title": "Two"},
            {"episodeId": "ep-1", "episode_number": 1, "title": "One"},
        ]})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    episodes = await adapter.episodes("sample-anime")
    assert [x["id"] for x in episodes] == ["sample-anime:1", "sample-anime:2"]
    assert episodes[0]["source_episode_id"] == "ep-1"
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
async def test_invalid_episode_id_is_rejected():
    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    adapter = ReAnimeAdapter(client)
    with pytest.raises(SourceRequestError) as error:
        await adapter.servers("https://attacker.example/path")
    assert error.value.status_code == 400
    await client.aclose()



@pytest.mark.asyncio
async def test_servers_fall_back_to_flix_when_watch_route_is_missing():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/watch/demo/1":
            return httpx.Response(404, json={"detail": "Not found"})
        if request.url.path == "/api/v1/anime/demo":
            return httpx.Response(200, json={"id": "demo", "anilist": 12345, "title": "Demo"})
        if request.url.path == "/api/flix/12345/1":
            return httpx.Response(200, json={"success": True, "servers": [
                {"serverName": "HD-2", "dataType": "sub", "dataLink": "https://flixcloud.cc/e/sub123?v=2"},
                {"serverName": "HD-1", "dataType": "dub", "dataLink": "https://flixcloud.cc/e/dub123?v=1"},
            ]})
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(base_url="https://reanime.cz", transport=httpx.MockTransport(handler))
    adapter = ReAnimeAdapter(client)
    result = await adapter.servers("demo:1")

    assert result["anilist_id"] == 12345
    assert result["sub"][0]["dataLink"].endswith("sub123?v=2")
    assert result["dub"][0]["dataLink"].endswith("dub123?v=1")
    flix_request = next(request for request in calls if request.url.path == "/api/flix/12345/1")
    assert flix_request.headers["referer"] == "https://reanime.cz/watch/demo?ep=1"
    await client.aclose()


@pytest.mark.asyncio
async def test_episode_id_parser_accepts_upstream_ep_identifier():
    assert ReAnimeAdapter._parse_episode_id("demo:ep-1") == ("demo", 1)
