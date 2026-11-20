import express from "express";
import cors from "cors";
import helmet from "helmet";
import morgan from "morgan";
import { config, corsOrigins } from "./config.js";
import { router } from "./routes.js";

const app = express();
app.disable("x-powered-by");
app.use(helmet());
app.use(cors({ origin: corsOrigins, methods: ["GET", "OPTIONS"] }));
app.use(express.json({ limit: "32kb" }));
app.use(morgan(config.NODE_ENV === "production" ? "combined" : "dev"));
app.use((req, res, next) => {
  const key = "__reanime_rate_" + (req.ip || "unknown");
  const now = Date.now();
  const store = (globalThis as any).__reanimeRateStore ??= new Map<string, { start: number; count: number }>();
  let entry = store.get(key);
  if (!entry || now - entry.start > config.RATE_LIMIT_WINDOW_MS) {
    entry = { start: now, count: 0 };
    store.set(key, entry);
  }
  entry.count++;
  res.setHeader("X-RateLimit-Limit", String(config.RATE_LIMIT_MAX));
  res.setHeader("X-RateLimit-Remaining", String(Math.max(0, config.RATE_LIMIT_MAX - entry.count)));
  if (entry.count > config.RATE_LIMIT_MAX) return res.status(429).json({ success: false, error: "RATE_LIMITED" });
  next();
});
app.get("/", (_req, res) => res.json({
  name: "ReAnime API",
  status: "ok",
  version: "1.0.0",
  docs: "/api/health",
  endpoints: [
    "GET /api/search?q=...",
    "GET /api/anime/:slug",
    "GET /api/episodes/:slug",
    "GET /api/servers/:slug/:episode",
    "GET /api/watch/:slug/:episode/sub",
    "GET /api/watch/:slug/:episode/dub",
    "GET /api/health"
  ]
}));
app.use("/api", router);
app.use((_req, res) => res.status(404).json({ success: false, error: "NOT_FOUND" }));

app.listen(config.PORT, config.HOST, () => {
  console.log(`ReAnime API listening on http://${config.HOST}:${config.PORT}`);
});