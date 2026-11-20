import { Router } from "express";
import { z } from "zod";
import { AppError } from "./errors.js";
import { getAnimeInfo, getEpisodeServers, getEpisodes, getPlaybackSources, searchAnime } from "./reanime/service.js";

export const router = Router();
const slugSchema = z.string().min(1).max(180).regex(/^[a-zA-Z0-9-]+$/);
const episodeSchema = z.coerce.number().int().min(1).max(10000);

router.get("/health", (_req, res) => res.json({ status: "ok", service: "reanime-api", provider: "reanime.to" }));
router.get("/search", async (req, res, next) => {
  try {
    const q = z.string().trim().min(1).max(120).parse(req.query.q);
    const limit = z.coerce.number().int().min(1).max(50).default(20).parse(req.query.limit);
    const offset = z.coerce.number().int().min(0).max(10000).default(0).parse(req.query.offset);
    res.json({ success: true, source: "reanime", data: await searchAnime(q, limit, offset) });
  } catch (e) { next(e); }
});
router.get("/anime/:slug", async (req, res, next) => {
  try { res.json({ success: true, source: "reanime", ...(await getAnimeInfo(slugSchema.parse(req.params.slug)) ) }); }
  catch (e) { next(e); }
});
router.get("/episodes/:slug", async (req, res, next) => {
  try { res.json({ success: true, source: "reanime", episodes: await getEpisodes(slugSchema.parse(req.params.slug)) }); }
  catch (e) { next(e); }
});
router.get("/servers/:slug/:episode", async (req, res, next) => {
  try {
    const slug = slugSchema.parse(req.params.slug);
    const episode = episodeSchema.parse(req.params.episode);
    res.json({ success: true, source: "reanime", episode, ...(await getEpisodeServers(slug, episode)) });
  } catch (e) { next(e); }
});
router.get("/watch/:slug/:episode/:type", async (req, res, next) => {
  try {
    const slug = slugSchema.parse(req.params.slug);
    const episode = episodeSchema.parse(req.params.episode);
    const type = z.enum(["sub", "dub"]).parse(req.params.type);
    res.json(await getPlaybackSources(slug, episode, type));
  } catch (e) { next(e); }
});
router.use((error: unknown, _req: any, res: any, _next: any) => {
  if (error instanceof z.ZodError) return res.status(400).json({ success: false, error: "VALIDATION_ERROR", details: error.issues });
  if (error instanceof AppError) return res.status(error.status).json({
    success: false, error: error.code, message: error.message,
    ...(error.upstreamStatus ? { upstreamStatus: error.upstreamStatus } : {})
  });
  console.error("Unhandled request error:", error);
  return res.status(500).json({ success: false, error: "INTERNAL_ERROR", message: "Unexpected server error" });
});