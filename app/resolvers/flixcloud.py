from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

ALLOWED_EMBED_HOSTS = {"flixcloud.cc", "www.flixcloud.cc"}
VIDEO_ID_PATTERNS = (
    re.compile(r"""(?:["']video_id["']|video_id)\s*:\s*["']([A-Za-z0-9_-]{8,128})["']""", re.I),
    re.compile(r"""(?:["']videoId["']|videoId)\s*:\s*["']([A-Za-z0-9_-]{8,128})["']""", re.I),
)
DECRYPTOR_PATH = Path(__file__).with_name("flixcloud_decrypt.mjs")


class FlixCloudResolveError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class FlixCloudResolver:
    """Resolve authorized FlixCloud embeds with a dedicated FlareSolverr browser session."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(75.0),
                follow_redirects=False,
                headers={"Accept": "application/json"},
            )

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def validate_embed_url(embed_url: str) -> tuple[str, str]:
        try:
            parsed = urlparse(embed_url)
        except ValueError as exc:
            raise FlixCloudResolveError("Invalid FlixCloud embed URL", 400) from exc
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_EMBED_HOSTS:
            raise FlixCloudResolveError("Only HTTPS FlixCloud embed URLs are accepted", 400)
        match = re.fullmatch(r"/e/([A-Za-z0-9_-]{6,128})/?", parsed.path)
        if not match:
            raise FlixCloudResolveError("Unrecognized FlixCloud embed URL format", 400)
        return match.group(1), embed_url

    @staticmethod
    def _extract_video_id(html: str) -> str | None:
        for pattern in VIDEO_ID_PATTERNS:
            match = pattern.search(html)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _safe_https_url(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 4096:
            return None
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        return value

    @classmethod
    def _safe_subtitles(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        result = []
        for item in value[:100]:
            if not isinstance(item, dict):
                continue
            url = cls._safe_https_url(item.get("url"))
            if not url:
                continue
            track: dict[str, Any] = {"url": url}
            for key in ("language", "label", "format"):
                if isinstance(item.get(key), str):
                    track[key] = item[key][:200]
            if isinstance(item.get("default"), bool):
                track["default"] = item["default"]
            result.append(track)
        return result

    async def _flaresolverr_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise FlixCloudResolveError("FlixCloud resolver HTTP client has not started", 503)
        base_url = settings.flixcloud_flaresolverr_url.rstrip("/")
        try:
            parsed = urlparse(base_url)
        except ValueError as exc:
            raise FlixCloudResolveError("Invalid FlareSolverr URL configuration", 500) from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise FlixCloudResolveError("Invalid FlareSolverr URL configuration", 500)
        try:
            response = await self._client.post(
                base_url + "/v1", json=payload, timeout=httpx.Timeout(75.0)
            )
        except httpx.TimeoutException as exc:
            raise FlixCloudResolveError("FlareSolverr request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise FlixCloudResolveError(
                "Could not reach FlareSolverr (" + type(exc).__name__ + ")", 503
            ) from exc
        if not response.is_success:
            raise FlixCloudResolveError("FlareSolverr returned HTTP " + str(response.status_code), 502)
        try:
            result = response.json()
        except ValueError as exc:
            raise FlixCloudResolveError("FlareSolverr returned invalid JSON", 502) from exc
        if not isinstance(result, dict) or result.get("status") != "ok":
            message = result.get("message", "unknown FlareSolverr error") if isinstance(result, dict) else "invalid response"
            raise FlixCloudResolveError("FlareSolverr failed: " + str(message)[:180], 502)
        return result

    async def _create_browser_session(self) -> str:
        session_id = "reanime-" + uuid.uuid4().hex
        await self._flaresolverr_post({"cmd": "sessions.create", "session": session_id})
        return session_id

    async def _destroy_browser_session(self, session_id: str) -> None:
        try:
            await self._flaresolverr_post({"cmd": "sessions.destroy", "session": session_id})
        except FlixCloudResolveError:
            pass

    @staticmethod
    def _challenge_check(body: str) -> None:
        lowered = body[:200_000].lower()
        if "just a moment" in lowered and "challenges.cloudflare.com" in lowered:
            raise FlixCloudResolveError(
                "FlareSolverr could not complete the FlixCloud browser challenge", 403
            )

    async def _flaresolverr_get(self, url: str, session_id: str, referer: str | None = None) -> str:
        """Solve Cloudflare on the origin, then fetch the embed without running its page JavaScript.

        The embed page can expose a one-use playback token. Navigating to that page in
        Chromium may execute provider scripts that consume the token before our decryptor
        requests it. Bootstrap the browser session on the origin instead, then reuse its
        clearance cookies and User-Agent for a plain HTTP fetch of the embed HTML.
        """
        parsed = urlparse(url)
        origin = parsed.scheme + "://" + parsed.netloc
        bootstrap = await self._flaresolverr_post({
            "cmd": "request.get",
            "url": origin + "/",
            "session": session_id,
            "maxTimeout": settings.flixcloud_flaresolverr_timeout_seconds,
            "headers": {"Accept": "text/html,application/xhtml+xml,*/*"},
        })
        solution = bootstrap.get("solution")
        if not isinstance(solution, dict):
            raise FlixCloudResolveError("FlareSolverr did not return an origin browser solution", 502)
        if solution.get("status") not in (200, 201):
            raise FlixCloudResolveError(
                "FlixCloud origin returned HTTP " + str(solution.get("status") or "unknown"), 502
            )
        bootstrap_body = solution.get("response")
        if isinstance(bootstrap_body, str):
            self._challenge_check(bootstrap_body)

        cookie_values: dict[str, str] = {}
        for cookie in solution.get("cookies", []):
            if not isinstance(cookie, dict):
                continue
            domain = str(cookie.get("domain") or "").lstrip(".").lower()
            name, value = cookie.get("name"), cookie.get("value")
            if domain in {"flixcloud.cc", "www.flixcloud.cc"} and isinstance(name, str) and isinstance(value, str):
                cookie_values[name] = value
        user_agent = solution.get("userAgent")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": referer or origin + "/",
        }
        if isinstance(user_agent, str) and user_agent:
            headers["User-Agent"] = user_agent
        if self._client is None:
            raise FlixCloudResolveError("FlixCloud resolver HTTP client has not started", 503)
        try:
            response = await self._client.get(
                url, headers=headers, cookies=cookie_values, timeout=httpx.Timeout(45.0)
            )
        except httpx.TimeoutException as exc:
            raise FlixCloudResolveError("FlixCloud embed request timed out", 504) from exc
        except httpx.HTTPError as exc:
            raise FlixCloudResolveError("Could not fetch FlixCloud embed (" + type(exc).__name__ + ")", 502) from exc
        if response.status_code not in (200, 201):
            raise FlixCloudResolveError(
                "FlixCloud embed returned HTTP " + str(response.status_code) + " after browser bootstrap", 502
            )
        body = response.text
        if not body:
            raise FlixCloudResolveError("FlixCloud returned an empty embed response", 502)
        if len(body.encode("utf-8")) > 5_000_000:
            raise FlixCloudResolveError("FlixCloud response exceeded 5 MB", 502)
        self._challenge_check(body)
        return body

    async def _run_decryptor(self, html: str, embed_url: str, session_id: str) -> dict[str, Any]:
        node = shutil.which("node")
        if not node:
            raise FlixCloudResolveError("Node.js 20+ is required for FlixCloud playback resolution", 503)
        if not DECRYPTOR_PATH.is_file():
            raise FlixCloudResolveError("FlixCloud decryptor is missing from deployment", 500)
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                node, str(DECRYPTOR_PATH), embed_url, session_id,
                settings.flixcloud_flaresolverr_url.rstrip("/"),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(html.encode("utf-8")), timeout=70
            )
        except asyncio.TimeoutError as exc:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            raise FlixCloudResolveError("FlixCloud playback resolution timed out", 504) from exc
        except OSError as exc:
            raise FlixCloudResolveError("Could not start the FlixCloud decryptor") from exc

        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()[:400]
            if "HTTP 410" in message:
                raise FlixCloudResolveError(
                    "FlixCloud rejected the one-time token (HTTP 410); no retry was attempted.", 410
                )
            if "HTTP 401" in message:
                raise FlixCloudResolveError("FlixCloud rejected the playback token request", 401)
            if "HTTP 403" in message:
                raise FlixCloudResolveError("FlixCloud forbade the playback token request", 403)
            if "HTTP 429" in message:
                raise FlixCloudResolveError("FlixCloud rate-limited the playback token request", 429)
            raise FlixCloudResolveError("FlixCloud decryption failed: " + (message or "unknown error"))
        try:
            payload = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FlixCloudResolveError("FlixCloud decryptor returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise FlixCloudResolveError("FlixCloud decryptor returned an unexpected response")
        return payload

    async def resolve(self, embed_url: str) -> dict[str, Any]:
        embed_id, canonical_url = self.validate_embed_url(embed_url)
        session_id = await self._create_browser_session()
        try:
            html = await self._flaresolverr_get(canonical_url, session_id)
            data = await self._run_decryptor(html, canonical_url, session_id)
        finally:
            await self._destroy_browser_session(session_id)

        stream_url = self._safe_https_url(data.get("url"))
        if not stream_url:
            raise FlixCloudResolveError("FlixCloud did not return a valid HTTPS stream URL")
        host = (urlparse(stream_url).hostname or "").lower()
        if host != "flixcloud.cc" and not host.endswith(".flixcloud.cc"):
            raise FlixCloudResolveError("FlixCloud returned a stream URL outside its provider domain", 502)
        return {
            "source": "flixcloud",
            "embed_id": embed_id,
            "video_id": str(data.get("video_id") or self._extract_video_id(html) or ""),
            "status": "playback_ready",
            "playback_ready": True,
            "url": stream_url,
            "subtitles": self._safe_subtitles(data.get("subtitles")),
            "thumbnails_vtt": self._safe_https_url(data.get("thumbnails_vtt")),
            "video_title": data.get("video_title") if isinstance(data.get("video_title"), str) else None,
            "intro_chapter": data.get("intro_chapter"),
            "outro_chapter": data.get("outro_chapter"),
        }
