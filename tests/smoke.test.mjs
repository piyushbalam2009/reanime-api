import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";

const port = 18000 + Math.floor(Math.random() * 10000);
const base = `http://127.0.0.1:${port}`;
let server;

before(async () => {
  server = spawn(process.execPath, ["dist/server.js"], {
    env: { ...process.env, NODE_ENV: "test", HOST: "127.0.0.1", PORT: String(port), CORS_ORIGINS: "*" },
    stdio: ["ignore", "pipe", "pipe"]
  });

  const started = Date.now();
  let lastError;
  while (Date.now() - started < 10000) {
    if (server.exitCode !== null) throw new Error(`API exited during startup (code ${server.exitCode})`);
    try {
      const response = await fetch(`${base}/api/health`);
      if (response.ok) return;
    } catch (error) { lastError = error; }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`API did not become ready: ${lastError ?? "timeout"}`);
});

after(() => {
  if (server && server.exitCode === null) server.kill("SIGTERM");
});

test("root advertises the public API routes", async () => {
  const response = await fetch(base);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.status, "ok");
  assert.ok(body.endpoints.includes("GET /api/watch/:slug/:episode/sub"));
  assert.ok(body.endpoints.includes("GET /api/watch/:slug/:episode/dub"));
});

test("health endpoint returns service identity", async () => {
  const response = await fetch(`${base}/api/health`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "ok",
    service: "reanime-api",
    provider: "reanime.to"
  });
});

test("search rejects an empty query", async () => {
  const response = await fetch(`${base}/api/search?q=`);
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error, "VALIDATION_ERROR");
});

test("anime route rejects invalid slugs", async () => {
  const response = await fetch(`${base}/api/anime/invalid_slug`);
  assert.equal(response.status, 400);
});

test("episodes route rejects invalid slugs", async () => {
  const response = await fetch(`${base}/api/episodes/invalid_slug`);
  assert.equal(response.status, 400);
});

test("servers route validates episode numbers", async () => {
  const response = await fetch(`${base}/api/servers/bleach-p76y5k/0`);
  assert.equal(response.status, 400);
});

test("watch route only accepts sub or dub", async () => {
  const response = await fetch(`${base}/api/watch/bleach-p76y5k/1/raw`);
  assert.equal(response.status, 400);
});

test("unknown routes return a stable 404 JSON response", async () => {
  const response = await fetch(`${base}/not-a-route`);
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { success: false, error: "NOT_FOUND" });
});

test("direct HLS normalizer accepts explicit HTTPS m3u8 entries and ignores embeds", async () => {
  const { extractDirectStreams } = await import("../dist/reanime/normalize.js");
  const result = extractDirectStreams({
    sources: [
      { url: "https://media.example/episode/master.m3u8", quality: "1080p" },
      { url: "https://media.example/episode/master.m3u8", quality: "1080p" },
      { url: "https://flixcloud.cc/e/abc123?v=2" },
      { url: "http://media.example/insecure/master.m3u8" }
    ]
  });
  assert.equal(result.length, 1);
  assert.equal(result[0].url, "https://media.example/episode/master.m3u8");
  assert.equal(result[0].quality, "1080p");
});
