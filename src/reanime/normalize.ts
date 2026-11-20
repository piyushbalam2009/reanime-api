import type { AudioType, ServerSource } from "../types.js";

type JsonRecord = Record<string, unknown>;

const record = (value: unknown): JsonRecord =>
  value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

export function normalizeEpisodes(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const data = record(value);
  if (Array.isArray(data.data)) return data.data;
  if (Array.isArray(data.episodes)) return data.episodes;
  if (Array.isArray(record(data.data).episodes)) return record(data.data).episodes as unknown[];
  return [];
}

function allowedEmbedUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase();
    const allowed = ["reanime.to", "flixcloud.cc"];
    if (url.protocol !== "https:" || !allowed.some((domain) => host === domain || host.endsWith("." + domain))) return null;
    if (url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}

function normalizeServer(item: JsonRecord, forcedType?: AudioType): ServerSource | null {
  const rawType = String(item.dataType ?? item.type ?? item.audioType ?? "").toLowerCase();
  const type: AudioType | null = forcedType ??
    (rawType.includes("dub") ? "dub" : rawType.includes("sub") ? "sub" : null);
  const dataLink = allowedEmbedUrl(item.dataLink ?? item.url ?? item.link ?? item.embedUrl);
  if (!type || !dataLink) return null;
  return {
    serverName: String(item.serverName ?? item.name ?? item.server ?? "Unknown").slice(0, 80),
    dataType: type,
    dataLink
  };
}

export function normalizeServers(value: unknown): {
  sub: ServerSource[];
  dub: ServerSource[];
  raw: JsonRecord;
} {
  const data = record(value);
  const candidates: Array<{ value: unknown; forced?: AudioType }> = [
    ...list(data.episode_links).map((value) => ({ value })),
    ...list(data.servers).map((value) => ({ value })),
    ...list(data.sub).map((value) => ({ value, forced: "sub" as const })),
    ...list(data.dub).map((value) => ({ value, forced: "dub" as const }))
  ];
  const result: Record<AudioType, ServerSource[]> = { sub: [], dub: [] };
  const seen = new Set<string>();

  for (const candidate of candidates) {
    const source = normalizeServer(record(candidate.value), candidate.forced);
    if (!source) continue;
    const key = source.dataType + "|" + source.serverName + "|" + source.dataLink;
    if (seen.has(key)) continue;
    seen.add(key);
    result[source.dataType].push(source);
  }

  // Prefer HD-2, then HD-1, while preserving unknown upstream server order.
  const rank = (name: string) => name.toUpperCase() === "HD-2" ? 0 : name.toUpperCase() === "HD-1" ? 1 : 2;
  result.sub.sort((a, b) => rank(a.serverName) - rank(b.serverName));
  result.dub.sort((a, b) => rank(a.serverName) - rank(b.serverName));
  return { ...result, raw: data };
}

export function directStreamMetadata(value: unknown): {
  url: string | null;
  subtitles: unknown[];
} {
  const data = record(value);
  const candidate = String(data.url ?? data.m3u8 ?? data.streamUrl ?? "").trim();
  let url: string | null = null;
  try {
    const parsed = new URL(candidate);
    if (parsed.protocol === "https:" && /\.m3u8(?:$|\?)/i.test(parsed.pathname + parsed.search)) {
      url = parsed.toString();
    }
  } catch {
    // The upstream may only provide an embed link.
  }
  return { url, subtitles: list(data.subtitles) };
}
