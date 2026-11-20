import time
import pytest

from app.main import settings
from app.security import ProxyTokenError, create_proxy_token, validate_source_url, verify_proxy_token

def test_rejects_http():
    with pytest.raises(ProxyTokenError):
        validate_source_url("http://reanime.cz/master.m3u8")

def test_rejects_unlisted_host():
    with pytest.raises(ProxyTokenError):
        validate_source_url("https://not-allowed.example/master.m3u8")

def test_token_round_trip(monkeypatch):
    monkeypatch.setattr(settings, "proxy_signing_secret", "unit-test-secret")
    monkeypatch.setattr(settings, "source_allowed_hosts", "reanime.cz")
    token = create_proxy_token("https://reanime.cz/master.m3u8")
    assert verify_proxy_token(token)["url"] == "https://reanime.cz/master.m3u8"

def test_expired_token(monkeypatch):
    monkeypatch.setattr(settings, "proxy_signing_secret", "unit-test-secret")
    monkeypatch.setattr(settings, "source_allowed_hosts", "reanime.cz")
    token = create_proxy_token("https://reanime.cz/master.m3u8", int(time.time()) - 1)
    with pytest.raises(ProxyTokenError):
        verify_proxy_token(token)
