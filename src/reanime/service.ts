import { AppError } from "../errors.js";
import { reanimeGet } from "./client.js";
import { directStreamMetadata, normalizeEpisodes, normalizeServers } from "./normalize.js";
import type { AudioType } from "../types.js";

async function withLegacyFallback<T>(primary: () => Promise<T>, legacy: () => Promise<T>): Promise<T> {
  try { return await primary(); }
  catch (error) {
    if (error instanceof AppError && error.status === 404) return legacy();
    throw error;
  }
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};
}
function unwrapAnime(value: unknown): Record<string, any> {
  const data = asRecord(value);
  return asRecord(data.anime ?? data.data ?? data);
}

export async function searchAnime(q: string, limit: number, offset: number) {
  // ReAnime's documented public search route is /api/search.
  // Keep /api/v1/search as a compatibility fallback for deployments that expose it.
  return withLegacyFallback(
    () => reanimeGet<unknown>("/api/search", { q, limit, offset }),
    () => reanimeGet<unknown>("/api/v1/search", { q, limit, offset })
  );
}

export async function getAnimeInfo(slug: string) {
  const [metaResult, eps] = await Promise.all([
    withLegacyFallback(
      () => reanimeGet<unknown>(`/api/v1/anime/${encodeURIComponent(slug)}`),
      () => reanimeGet<unknown>(`/api/watch/${encodeURIComponent(slug)}/1`)
    ),
    getEpisodes(slug)
  ]);
  const anime = unwrapAnime(metaResult);
  const anilistId = Number(anime.anilist_id ?? anime.anilist ?? anime.anilistId);
  return { anime, episodes: eps, anilist_id: Number.isFinite(anilistId) && anilistId > 0 ? anilistId : null };
}

export async function getEpisodes(slug: string) {
  const data = await withLegacyFallback(
    () => reanimeGet<unknown>(`/api/v1/episodes/${encodeURIComponent(slug)}`),
    () => reanimeGet<unknown>(`/api/episodes/${encodeURIComponent(slug)}`)
  );
  return normalizeEpisodes(data);
}

export async function getEpisodeServers(slug: string, episode: number) {
  const watch = await reanimeGet<Record<string, any>>(`/api/watch/${encodeURIComponent(slug)}/${episode}`);
  const anime = unwrapAnime(watch.anime);
  const anilist = Number(anime.anilist_id ?? anime.anilist ?? anime.anilistId);
  let flix: Record<string, any> = {};

  if (Number.isFinite(anilist) && anilist > 0) {
    try { flix = await reanimeGet<Record<string, any>>(`/api/flix/${anilist}/${episode}`); }
    catch (error) { if (!(error instanceof AppError)) throw error; }
  }

  const merged = {
    ...watch,
    ...flix,
    sub: [...(Array.isArray(watch.sub) ? watch.sub : []), ...(Array.isArray(flix.sub) ? flix.sub : [])],
    dub: [...(Array.isArray(watch.dub) ? watch.dub : []), ...(Array.isArray(flix.dub) ? flix.dub : [])],
    episode_links: [
      ...(Array.isArray(watch.episode_links) ? watch.episode_links : []),
      ...(Array.isArray(flix.episode_links) ? flix.episode_links : []),
      ...(Array.isArray(watch.servers) ? watch.servers : []),
      ...(Array.isArray(flix.servers) ? flix.servers : [])
    ]
  };
  const normalized = normalizeServers(merged);
  const playback = directStreamMetadata(merged);

  return {
    anime: watch.anime ?? flix.anime ?? null,
    current: watch.current ?? watch.episode ?? flix.current ?? null,
    duration: watch.duration ?? flix.duration ?? null,
    anilist_id: Number.isFinite(anilist) && anilist > 0 ? anilist : null,
    intro: {
      start: watch.intro_start ?? watch.intro?.start ?? flix.intro_start ?? null,
      end: watch.intro_end ?? watch.intro?.end ?? flix.intro_end ?? null
    },
    outro: {
      start: watch.outro_start ?? watch.outro?.start ?? flix.outro_start ?? null,
      end: watch.outro_end ?? watch.outro?.end ?? flix.outro_end ?? null
    },
    sub: normalized.sub,
    dub: normalized.dub,
    streams: playback.streams,
    subtitles: playback.subtitles
  };
}

export async function getPlaybackSources(slug: string, episode: number, type: AudioType) {
  const result = await getEpisodeServers(slug, episode);
  const selected = result[type];
  // Direct HLS URLs are returned only when the upstream JSON explicitly supplies them.
  // Embed pages, obfuscated player scripts, DRM licenses and protected tokens are not bypassed.
  const streams = result.streams;
  return {
    success: true,
    source: "reanime",
    anime: result.anime,
    episode: { number: episode, type },
    servers: selected,
    streams,
    subtitles: result.subtitles,
    message: streams.length
      ? "Direct HLS sources were present in the upstream response."
      : selected.length
        ? "No direct HLS URL was supplied by the upstream API; authorized embed references are available."
        : `No ${type} servers are available for this episode.`,
    intro: result.intro,
    outro: result.outro
  };
}
