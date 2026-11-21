#!/usr/bin/env node
import crypto from "node:crypto";
import vm from "node:vm";

const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const b64 = (s) => Buffer.from(s || "", "base64");

function fieldNames(seed) {
  let e = seed;
  for (let i = 0; i < 3; i++) e = sha(e + i);
  let l = e;
  for (let i = 0; i < 3; i++) l = sha(l + i);
  return {
    key: "kf_" + e.slice(8, 16),
    iv: "ivf_" + e.slice(16, 24),
    container: "cd_" + e.slice(24, 32),
    array: "ad_" + e.slice(32, 40),
    object: "od_" + e.slice(40, 48),
    token: e.slice(48, 64) + "_" + e.slice(56, 64),
    fragment: l.slice(0, 16) + "_" + l.slice(16, 24),
  };
}
function extractData(html) {
  const marker = html.match(/\{type:"data",data:(\{)/);
  if (!marker) throw new Error("FlixCloud SSR data block not found");
  const start = html.indexOf("{", marker.index + marker[0].length - 1);
  let depth = 0, quote = null, escaped = false;
  for (let i = start; i < html.length; i++) {
    const ch = html[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; continue; }
    if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) return html.slice(start, i + 1);
  }
  throw new Error("FlixCloud SSR data block incomplete");
}
async function requestTokenMetadata(flaresolverrUrl, sessionId, embedUrl, token) {
  const tokenUrl = "https://flixcloud.cc/api/m3u8/" + encodeURIComponent(token);
  const response = await fetch(flaresolverrUrl + "/v1", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify({
      cmd: "request.get",
      url: tokenUrl,
      session: sessionId,
      maxTimeout: 60000,
      headers: { "Accept": "application/json", "Referer": embedUrl },
    }),
    signal: AbortSignal.timeout(65000),
  });
  if (!response.ok) throw new Error("FlareSolverr token request returned HTTP " + response.status);
  const result = await response.json();
  if (result?.status !== "ok" || !result?.solution) {
    throw new Error("FlareSolverr could not complete the playback token request");
  }
  const status = result.solution.status;
  if (status !== 200 && status !== 201) {
    throw new Error("FlixCloud token metadata returned HTTP " + String(status || "unknown"));
  }
  const body = result.solution.response;
  if (typeof body !== "string" || !body) throw new Error("FlixCloud token metadata response was empty");
  if (body.length > 1000000) throw new Error("FlixCloud token metadata response was too large");
  try {
    return JSON.parse(body);
  } catch {
    if (body.toLowerCase().includes("just a moment")) {
      throw new Error("FlareSolverr could not complete the Cloudflare challenge for token metadata");
    }
    throw new Error("FlixCloud token metadata was not valid JSON");
  }
}
async function main() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const html = Buffer.concat(chunks).toString("utf8");
  if (!html || html.length > 5_000_000) throw new Error("Embed HTML empty or too large");
  const embedUrl = process.argv[2] || "";
  const sessionId = process.argv[3] || "";
  const flaresolverrUrl = process.argv[4] || "";
  if (!/^https:\/\/(www\.)?flixcloud\.cc\/e\//.test(embedUrl)) throw new Error("Invalid embed URL");
  if (!/^reanime-[a-f0-9]{32}$/.test(sessionId)) throw new Error("Invalid browser session identifier");
  if (!/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(flaresolverrUrl)) {
    throw new Error("FlareSolverr must be configured on localhost");
  }

  const data = vm.runInNewContext("(" + extractData(html) + ")", Object.create(null), {
    timeout: 1000, codeGeneration: { strings: false, wasm: false },
  });
  const seed = data.obfuscation_seed;
  if (typeof seed !== "string" || seed.length < 16) throw new Error("Obfuscation seed missing");
  const f = fieldNames(seed);
  const entry = data.obfuscated_crypto_data?.[f.container]?.[f.array]?.[0]?.[f.object];
  const fragment1 = b64(entry?.[f.key]);
  const iv = b64(entry?.[f.iv]);
  const fragment2 = b64(data[f.fragment]);
  const token = data[f.token];
  if (!token || typeof token !== "string" || !data.w_payload) throw new Error("One-time token or WASM payload missing");

  // One-time token request: exactly once, using the same authorized browser session.
  const tokenData = await requestTokenMetadata(flaresolverrUrl, sessionId, embedUrl, token);
  const encryptedUrl = b64(tokenData[sha(token + "vid").slice(0, 10)]);
  const tokenBytes = b64(tokenData[sha(token + "key").slice(0, 10)]);
  if (!fragment1.length || !iv.length || !encryptedUrl.length || !tokenBytes.length) {
    throw new Error("Required FlixCloud token fields missing");
  }

  const { instance } = await WebAssembly.instantiate(b64(data.w_payload));
  const { _s, _r, memory } = instance.exports;
  if (typeof _s !== "function" || typeof _r !== "function" || !memory) throw new Error("Unexpected WASM exports");
  const heap = new Uint8Array(memory.buffer), n = fragment1.length;
  const [a, b, c, out] = [1000, 1000 + n, 1000 + 2*n, 1000 + 3*n];
  if (out + n > heap.length || fragment2.length < n || tokenBytes.length < n) throw new Error("Invalid WASM input lengths");
  heap.set(fragment1, a); heap.set(fragment2, b); heap.set(tokenBytes, c);
  _s(Number.parseInt(seed.slice(0, 8), 16));
  _r(a, b, c, out, n);
  const wasmOutput = Buffer.from(heap.subarray(out, out + n));

  const keyMaterial = crypto.pbkdf2Sync(wasmOutput, seed, 1000, 32, "sha256");
  for (let i = 0; i < 32; i++) keyMaterial[i] ^= seed.charCodeAt(i % seed.length);
  const aesKey = crypto.createHash("sha256").update(keyMaterial).digest();
  const decipher = crypto.createDecipheriv("aes-256-cbc", aesKey, iv);
  const url = Buffer.concat([decipher.update(encryptedUrl), decipher.final()]).toString("utf8").trim();
  if (!url.startsWith("https://")) throw new Error("Decrypted stream URL is not HTTPS");

  process.stdout.write(JSON.stringify({
    url, subtitles: Array.isArray(data.subtitles) ? data.subtitles : [],
    thumbnails_vtt: data.thumbnails_vtt ?? null, video_title: data.video_title ?? null,
    intro_chapter: data.intro_chapter ?? null, outro_chapter: data.outro_chapter ?? null,
    video_id: data.video_id ?? null,
  }));
}
main().catch((e) => {
  process.stderr.write(String(e?.message ?? e).slice(0, 300) + "\n");
  process.exit(1);
});
