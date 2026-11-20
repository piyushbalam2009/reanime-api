import { AppError } from "../errors.js";
import { reanimeGet } from "./client.js";
import { normalizeEpisodes, normalizeServers } from "./normalize.js";
import type { AudioType } from "../types.js";

async function withLegacyFallback<T>(primary: () => Promise<T>, legacy: () => Promise<T>): Promise<T> {
  try {
    return await primary();
  } catch (error) {
    if (error instanceof AppError && error.status === 404) return legacy();
    throw error;
  }
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, any>
    : {};
}

function unwrapAnime(value: unknown): Record<string, any> {
  const data = asRecord(value);
  return asRecord(data.anime ?? data.data ?? data);
}

export async function searchAnime(q: string, limit: number, offset: number) {
  return withLegacyFallback(
    () => reanimeGet<unknown>("/api/v1/search", { q, limit, offset }),
    () => reanimeGet<unknown>("/api/search", { q, limit, offset })
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
  const anilistValue = anime.anilist_id ?? anime.anilist ?? anime.anilistId;
  const anilistId = Number(anilistValue);
  return {
    anime,
    episodes: eps,
    anilist_id: Number.isFinite(anilistId) && anilistId > 0 ? anilistId : null
  };
}

export async function getEpisodes(slug: string) {
  const data = await withLegacyFallback(
    () => reanimeGet<unknown>(`/api/v1/episodes/${encodeURIComponent(slug)}`),
    () => reanimeGet<unknown>(`/api/episodes/${encodeURIComponent(slug)}`)
  );
  return normalizeEpisodes(data);
}

export async function getEpisodeServers(slug: string, episode: number) {
  const watch = await reanimeGet<Record<string, any>>(
    `/api/watch/${encodeURIComponent(slug)}/${episode}`
  );
  const anime = unwrapAnime(watch.anime);
  const anilistValue = anime.anilist_id ?? anime.anilist ?? anime.anilistId;
  const anilist = Number(anilistValue);
  let flix: Record<string, any> = {};

  if (Number.isFinite(anilist) && anilist > 0) {
    try {
      flix = await reanimeGet<Record<string, any>>(`/api/flix/${anilist}/${episode}`);
    } catch (error) {
      // The primary watch endpoint may still contain usable episode links.
      if (!(error instanceof AppError)) throw error;
    }
  }

  const merged = {
    ...watch,
    sub: [
      ...(Array.isArray(watch.sub) ? watch.sub : []),
      ...(Array.isArray(flix.sub) ? flix.sub : [])
    ],
    dub: [
      ...(Array.isArray(watch.dub) ? watch.dub : []),
      ...(Array.isArray(flix.dub) ? flix.dub : [])
    ],
    episode_links: [
      ...(Array.isArray(watch.episode_links) ? watch.episode_links : []),
      ...(Array.isArray(flix.servers) ? flix.servers : [])
    ]
  };
  const normalized = normalizeServers(merged);

  return {
    anime: watch.anime ?? null,
    current: watch.current ?? watch.episode ?? null,
    duration: watch.duration ?? null,
    anilist_id: Number.isFinite(anilist) && anilist > 0 ? anilist : null,
    intro: {
      start: watch.intro_start ?? watch.intro?.start ?? null,
      end: watch.intro_end ?? watch.intro?.end ?? null
    },
    outro: {
      start: watch.outro_start ?? watch.outro?.start ?? null,
      end: watch.outro_end ?? watch.outro?.end ?? null
    },
    sub: normalized.sub,
    dub: normalized.dub
  };
}

export async function getPlaybackSources(slug: string, episode: number, type: AudioType) {
  const servers = await getEpisodeServers(slug, episode);
  const selected = servers[type];
  return {
    success: true,
    source: "reanime",
    anime: servers.anime,
    episode: { number: episode, type },
    servers: selected,
    // ReAnime/third-party embed hosts may apply their own playback protections.
    // Return authorized embed references; do not bypass DRM or decrypt protected tokens.
    streams: [],
    subtitles: [],
    message: selected.length
      ? "Server references found. Use the authorized embed in a compatible player."
      : `No ${type} servers are available for this episode.`,
    intro: servers.intro,
    outro: servers.outro
  };
}
