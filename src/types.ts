export type AudioType = "sub" | "dub";
export interface AnimeSearchItem {
  slug: string;
  title: string;
  url?: string;
  image?: string | null;
  year?: number | null;
  type?: string | null;
  raw?: unknown;
}
export interface SubtitleTrack {
  url: string;
  language: string;
  format: string;
  default?: boolean;
}
export interface ServerSource {
  serverName: string;
  dataType: AudioType;
  dataLink: string;
  directUrl?: string;
}
export interface ApiErrorShape {
  error: string;
  message: string;
  status: number;
  upstreamStatus?: number;
}