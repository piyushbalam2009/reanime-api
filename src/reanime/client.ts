import { config } from "../config.js";
import { AppError } from "../errors.js";

const BASE = config.REANIME_BASE_URL.replace(/\/$/, "");
const ALLOWED_HOSTS = new Set(["reanime.to", "www.reanime.to"]);
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

async function fetchUpstream(url: URL, signal: AbortSignal): Promise<Response> {
  let current = url;

  // Follow only a small number of redirects, and only within the configured
  // ReAnime host family. This avoids rejecting normal canonical-host redirects
  // while preventing an upstream redirect from turning this into an open proxy.
  for (let hop = 0; hop <= 4; hop++) {
    const response = await fetch(current, {
      headers: {
        Accept: "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": USER_AGENT,
        Referer: "https://reanime.to/",
        Origin: "https://reanime.to"
      },
      signal,
      redirect: "manual"
    });

    if (![301, 302, 303, 307, 308].includes(response.status)) return response;

    const location = response.headers.get("location");
    if (!location) return response;
    if (hop === 4) throw new AppError(502, "ReAnime redirected too many times", "UPSTREAM_REDIRECT_LIMIT");

    const next = new URL(location, current);
    if (next.protocol !== "https:" || !ALLOWED_HOSTS.has(next.hostname)) {
      throw new AppError(502, "ReAnime redirected to an untrusted host", "UPSTREAM_REDIRECT_BLOCKED");
    }
    current = next;
  }

  throw new AppError(502, "ReAnime redirect handling failed", "UPSTREAM_REDIRECT_ERROR");
}

export async function reanimeGet<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  // Only relative, fixed API paths are accepted; callers cannot turn this client into an SSRF proxy.
  if (!path.startsWith("/api/") || path.includes("..") || path.startsWith("//")) {
    throw new AppError(400, "Invalid upstream API path", "INVALID_UPSTREAM_PATH");
  }

  const url = new URL(path, BASE);
  if (params) for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.REQUEST_TIMEOUT_MS);
  try {
    const response = await fetchUpstream(url, controller.signal);
    if (response.status === 404) {
      throw new AppError(404, "The requested ReAnime resource was not found", "UPSTREAM_NOT_FOUND", 404);
    }
    if (!response.ok) {
      const detail = (await response.text()).slice(0, 240).replace(/\s+/g, " ");
      console.warn("[reanime] upstream HTTP error", {
        path,
        status: response.status,
        detail
      });
      throw new AppError(502, "ReAnime upstream returned an error", "UPSTREAM_ERROR", response.status);
    }

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json") && !contentType.includes("+json")) {
      const sample = (await response.text()).slice(0, 160).replace(/\s+/g, " ");
      console.warn("[reanime] unexpected upstream content type", { path, contentType, sample });
      throw new AppError(502, "Unexpected upstream content type", "UPSTREAM_BAD_CONTENT");
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new AppError(504, "ReAnime request timed out", "UPSTREAM_TIMEOUT");
    }
    const cause = error instanceof Error ? error.message : String(error);
    console.error("[reanime] upstream network failure", { path, cause });
    throw new AppError(502, "Could not reach ReAnime; check server logs for the network cause", "UPSTREAM_UNAVAILABLE");
  } finally {
    clearTimeout(timeout);
  }
}
