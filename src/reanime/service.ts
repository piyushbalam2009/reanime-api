import { reanimeGet } from "./client.js";
import { normalizeEpisodes, normalizeServers } from "./normalize.js";
import type { AudioType } from "../types.js";

export async function searchAnime(q: string, limit: number, offset: number) {
  return reanimeGet<unknown>("/api/search", { q, limit, offset });
}
export async function getAnimeInfo(slug: string) {
  const [meta, eps] = await Promise.all([
    reanimeGet<Record<string, any>>(`/api/watch/${encodeURIComponent(slug)}/1`),
    reanimeGet<unknown>(`/api/episodes/${encodeURIComponent(slug)}`)
  ]);
  const anime = meta.anime ?? {};
  return {
    anime,
    episodes: normalizeEpisodes(eps),
    anilist_id: anime.anilist ? Number(anime.anilist) : null
  };
}
export async function getEpisodes(slug: string) {
  return normalizeEpisodes(await reanimeGet<unknown>(`/api/episodes/${encodeURIComponent(slug)}`));
}
export async function getEpisodeServers(slug: string, episode: number) {
  const watch = await reanimeGet<Record<string, any>>(
    `/api/watch/${encodeURIComponent(slug)}/${episode}`
  );
  const anilist = watch.anime?.anilist;
  let flix: Record<string, any> = {};
  if (anilist) {
    try {
      flix = await reanimeGet<Record<string, any>>(`/api/flix/${Number(anilist)}/${episode}`);
    } catch {
      // The watch endpoint remains useful when the optional Flix server index is unavailable.
    }
  }
  const merged = {
    ...watch,
    episode_links: [
      ...(Array.isArray(watch.episode_links) ? watch.episode_links : []),
      ...(Array.isArray(flix.servers) ? flix.servers : [])
    ]
  };
  const normalized = normalizeServers(merged);
  return {
    anime: watch.anime ?? null,
    current: watch.current ?? null,
    duration: watch.duration ?? null,
    anilist_id: anilist ? Number(anilist) : null,
    intro: { start: watch.intro_start ?? null, end: watch.intro_end ?? null },
    outro: { start: watch.outro_start ?? null, end: watch.outro_end ?? null },
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
    // ReAnime currently supplies third-party embed references for some servers.
    // This API intentionally does not bypass embed protections or decrypt protected media tokens.
    streams: [],
    subtitles: [],
    message: selected.length
      ? "Server references found. Open the authorized embed in a compatible player."
      : `No ${type} servers are available for this episode.`,
    intro: servers.intro,
    outro: servers.outro
  };
}