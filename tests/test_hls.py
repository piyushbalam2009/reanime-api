from app.config import settings
from streaming_proxy.main import rewrite_playlist


def test_rewrites_variant_and_subtitle_uris(monkeypatch):
    monkeypatch.setattr(settings, "proxy_signing_secret", "test-secret")
    monkeypatch.setattr(settings, "source_allowed_hosts", "reanime.cz")
    monkeypatch.setattr(settings, "stream_proxy_base_url", "https://stream.example")
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",URI="subs/en.m3u8"
"""
    rewritten = rewrite_playlist(playlist, "https://reanime.cz/hls/master.m3u8", 4102444800)
    assert "https://stream.example/proxy/" in rewritten
    assert "720p/index.m3u8" not in rewritten
    assert 'URI="https://stream.example/proxy/' in rewritten
