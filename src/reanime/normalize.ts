import type { AudioType, ServerSource } from "../types.js";

const record = (value: unknown): Record<string, any> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};

export function normalizeEpisodes(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const data = record(value);
  if (Array.isArray(data.data)) return data.data;
  if (Array.isArray(data.episodes)) return data.episodes;
  return [];
}

export function normalizeServers(value: unknown): { sub: ServerSource[]; dub: ServerSource[]; raw: Record<string, any> } {
  const data = record(value);
  const links = Array.isArray(data.episode_links) ? data.episode_links : [];
  const flixServers = Array.isArray(data.servers) ? data.servers : [];
  const all = [...links, ...flixServers].map(record);
  const seen = new Set<string>();
  const result: Record<AudioType, ServerSource[]> = { sub: [], dub: [] };
  for (const item of all) {
    const rawType = String(item.dataType ?? item.type ?? "").toLowerCase();
    const type: AudioType | null = rawType.includes("dub") ? "dub" : rawType.includes("sub") ? "sub" : null;
    const dataLink = String(item.dataLink ?? item.url ?? item.link ?? "").trim();
    if (!type || !dataLink) continue;
    // Preserve only recognized ReAnime/FlixCloud embed references; never expose arbitrary URLs as fetch targets.
    let parsed: URL;
    try { parsed = new URL(dataLink); } catch { continue; }
    if (!["reanime.to", "www.reanime.to", "flixcloud.cc", "www.flixcloud.cc"].includes(parsed.hostname)) continue;
    const key = type + "|" + dataLink;
    if (seen.has(key)) continue;
    seen.add(key);
    result[type].push({
      serverName: String(item.serverName ?? item.name ?? "Unknown"),
      dataType: type,
      dataLink
    });
  }
  return { ...result, raw: data };
}

export function directStreamMetadata(value: unknown): { url: string | null; subtitles: unknown[] } {
  const data = record(value);
  const candidate = String(data.url ?? data.m3u8 ?? data.streamUrl ?? "").trim();
  let url: string | null = null;
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol === "https:" && /\.m3u8(?:$|\?)/i.test(parsed.pathname + parsed.search)) url = parsed.toString();
  } catch { /* no direct URL was supplied by upstream */ }
  return { url, subtitles: Array.isArray(data.subtitles) ? data.subtitles : [] };
}