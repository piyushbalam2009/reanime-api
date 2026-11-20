import { config } from "../config.js";
import { AppError } from "../errors.js";

const BASE = config.REANIME_BASE_URL.replace(/\/$/, "");
const USER_AGENT = "ReAnimeAPI/1.0 (+self-hosted integration)";

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
    const response = await fetch(url, {
      headers: { Accept: "application/json", "User-Agent": USER_AGENT },
      signal: controller.signal,
      redirect: "error"
    });
    if (response.status === 404) throw new AppError(404, "The requested ReAnime resource was not found", "UPSTREAM_NOT_FOUND", 404);
    if (!response.ok) throw new AppError(502, "ReAnime upstream returned an error", "UPSTREAM_ERROR", response.status);
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) throw new AppError(502, "Unexpected upstream content type", "UPSTREAM_BAD_CONTENT");
    return await response.json() as T;
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof Error && error.name === "AbortError") throw new AppError(504, "ReAnime request timed out", "UPSTREAM_TIMEOUT");
    throw new AppError(502, "Could not reach ReAnime", "UPSTREAM_UNAVAILABLE");
  } finally {
    clearTimeout(timeout);
  }
}