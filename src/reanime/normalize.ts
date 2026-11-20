import type { AudioType, ServerSource, SubtitleTrack } from "../types.js";

type JsonRecord = Record<string, unknown>;
const record = (value: unknown): JsonRecord =>
  value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

export function normalizeEpisodes(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const data = record(value);
  if (Array.isArray(data.data)) return data.data;
  if (Array.isArray(data.episodes)) return data.episodes;
  if (Array.isArray(record(data.data).episodes)) return record(data.data).episodes as unknown[];
  return [];
}

function safeHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url.toString();
  } catch { return null; }
}

function allowedEmbedUrl(value: unknown): string | null {
  const safe = safeHttpsUrl(value);
  if (!safe) return null;
  const host = new URL(safe).hostname.toLowerCase();
  const allowed = ["reanime.to", "flixcloud.cc"];
  return allowed.some((domain) => host === domain || host.endsWith("." + domain)) ? safe : null;
}

function normalizeServer(item: JsonRecord, forcedType?: AudioType): ServerSource | null {
  const rawType = String(item.dataType ?? item.type ?? item.audioType ?? "").toLowerCase();
  const type: AudioType | null = forcedType ??
    (rawType.includes("dub") ? "dub" : rawType.includes("sub") ? "sub" : null);
  const dataLink = allowedEmbedUrl(item.dataLink ?? item.url ?? item.link ?? item.embedUrl);
  if (!type || !dataLink) return null;
  return { serverName: String(item.serverName ?? item.name ?? item.server ?? "Unknown").slice(0, 80), dataType: type, dataLink };
}

export function normalizeServers(value: unknown): { sub: ServerSource[]; dub: ServerSource[]; raw: JsonRecord } {
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
    if (!seen.has(key)) { seen.add(key); result[source.dataType].push(source); }
  }
  const rank = (name: string) => name.toUpperCase() === "HD-2" ? 0 : name.toUpperCase() === "HD-1" ? 1 : 2;
  result.sub.sort((a, b) => rank(a.serverName) - rank(b.serverName));
  result.dub.sort((a, b) => rank(a.serverName) - rank(b.serverName));
  return { ...result, raw: data };
}

const URL_KEYS = ["url", "m3u8", "src", "file", "streamUrl", "playbackUrl", "hls", "hlsUrl"];
const ARRAY_KEYS = ["sources", "streams", "qualities", "files"];

function isHlsUrl(value: unknown): string | null {
  const safe = safeHttpsUrl(value);
  if (!safe) return null;
  return /\.m3u8$/i.test(new URL(safe).pathname) ? safe : null;
}

function collectHls(value: unknown, output: Array<{ url: string; quality: string | null; label: string | null }>, depth = 0): void {
  if (depth > 7) return;
  if (Array.isArray(value)) { for (const item of value) collectHls(item, output, depth + 1); return; }
  const obj = record(value);
  if (!Object.keys(obj).length) return;
  for (const key of URL_KEYS) {
    const url = isHlsUrl(obj[key]);
    if (url) {
      const quality = obj.quality ?? obj.resolution ?? obj.label ?? obj.name;
      output.push({ url, quality: quality == null ? null : String(quality).slice(0, 32), label: obj.label == null ? null : String(obj.label).slice(0, 64) });
    }
  }
  for (const key of ARRAY_KEYS) if (obj[key] !== undefined) collectHls(obj[key], output, depth + 1);
  for (const key of ["data", "result", "source", "player", "stream"]) {
    if (obj[key] && typeof obj[key] === "object") collectHls(obj[key], output, depth + 1);
  }
}

export function extractDirectStreams(value: unknown) {
  const found: Array<{ url: string; quality: string | null; label: string | null }> = [];
  collectHls(value, found);
  const seen = new Set<string>();
  return found.filter((item) => { if (seen.has(item.url)) return false; seen.add(item.url); return true; })
    .map((item) => ({ ...item, format: "hls" as const }));
}

export function extractSubtitles(value: unknown): SubtitleTrack[] {
  const data = record(value);
  const candidates = [...list(data.subtitles), ...list(data.tracks), ...list(record(data.data).subtitles)];
  const result: SubtitleTrack[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    const item = record(candidate);
    const url = safeHttpsUrl(item.url ?? item.file ?? item.src);
    if (!url) continue;
    const match = (new URL(url).pathname + new URL(url).search).match(/\.(vtt|srt|ass)(?:$|\?)/i);
    if (!match || seen.has(url)) continue;
    seen.add(url);
    result.push({ url, language: String(item.language ?? item.lang ?? item.label ?? "und").slice(0, 32), format: match[1].toLowerCase(), ...(item.default === true || item.isDefault === true ? { default: true } : {}) });
  }
  return result;
}

export function directStreamMetadata(value: unknown) {
  return { streams: extractDirectStreams(value), subtitles: extractSubtitles(value) };
}
