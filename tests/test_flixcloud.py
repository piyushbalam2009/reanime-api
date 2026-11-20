import pytest

from app.flixcloud import FlixCloudResolver


def test_extracts_direct_master_and_subtitle_urls_without_changing_tokens():
    master = "https://fetch9.flixcloud.cc/_v7/abc/master.m3u8?token=abc%2F123"
    subtitle = "https://flixcloud.cc/subtitles/abc/eng_4.ass?token=sub456"
    payload = {
        "sources": [{"url": master}],
        "tracks": [{"file": subtitle, "label": "English"}],
    }

    found_master, subtitles = FlixCloudResolver._extract_media(payload)

    assert found_master == master
    assert subtitles[0]["url"] == subtitle


def test_opaque_payload_is_not_mistaken_for_a_media_url():
    payload = {
        "30e28a3b46": "VGhpcy1pcy1ub3QtYS11cmw=",
        "2c80923e7a": "YW5vdGhlci1vcGFxdWUtdmFsdWU=",
        "metadata": {"version": "2.1"},
    }

    master, subtitles = FlixCloudResolver._extract_media(payload)

    assert master is None
    assert subtitles == []


def test_rejects_non_flixcloud_embed_hosts():
    with pytest.raises(Exception):
        # The resolver's URL validation rejects unrelated embed hosts.
        import asyncio
        asyncio.run(FlixCloudResolver().resolve("https://attacker.example/e/123"))
