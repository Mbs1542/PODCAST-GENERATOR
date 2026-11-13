"use strict";
/* M.B.S Studio phone app worker. Scope: /m/.
   - The page itself: network first (it carries the server's current API token), the last copy offline.
   - Its CSS/JS/icons: from the versioned shell cache.
   - /m/offline/...: episodes and texts the user saved on the phone (the page writes them to the
     "mbs-m-offline" cache). Audio answers Range requests, which iOS needs to play and seek.
   - /api/...: never touched; it always goes to the server. */

const VERSION = "__MBS_VERSION__";
const SHELL = `mbs-m-shell-${VERSION}`;
const OFFLINE = "mbs-m-offline";
const SHELL_FILES = [
  `/static/m/m.css?v=${VERSION}`, `/static/m/m.js?v=${VERSION}`,
  "/static/m/icon-192.png", "/static/m/icon-512.png", "/static/m/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await cache.addAll(SHELL_FILES);
    // The page too: the first visit loads it before this worker exists, and without a copy the
    // app would not open offline at all. Every later online visit refreshes the copy (and its token).
    const page = await fetch("/m/", { cache: "no-store" });
    if (page.ok && page.type === "basic" && !page.redirected) await cache.put("/m/", page);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith("mbs-m-shell-") && name !== SHELL) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/m/offline/")) return event.respondWith(fromOffline(req, url));
  if (url.pathname === "/m/" || req.mode === "navigate") return event.respondWith(page(req));
  if (url.pathname.startsWith("/static/m/")) return event.respondWith(shellFile(req));
});

async function page(req) {
  try {
    const res = await fetch(req);
    // Only a real page is kept; an expired Access session answers with a redirect to its sign-in
    if (res.ok && res.type === "basic" && new URL(req.url).pathname === "/m/") {
      const cache = await caches.open(SHELL);
      await cache.put("/m/", res.clone());
    }
    return res;
  } catch (err) {
    const cached = await caches.match("/m/");
    if (cached) return cached;
    throw err;
  }
}

async function shellFile(req) {
  const cached = await caches.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res.ok) (await caches.open(SHELL)).put(req, res.clone());
  return res;
}

async function fromOffline(req, url) {
  const hit = await (await caches.open(OFFLINE)).match(url.pathname);
  if (!hit) return new Response("not saved on this phone", { status: 404 });
  const range = req.headers.get("range");
  if (!range) return hit;
  const blob = await hit.blob();
  const size = blob.size;
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start = 0;
  let end = size - 1;
  if (m && m[1] === "" && m[2] !== "") {  // "bytes=-500": the last 500 bytes
    start = Math.max(0, size - Number(m[2]));
  } else if (m) {
    start = Number(m[1] || 0);
    if (m[2] !== "") end = Math.min(Number(m[2]), size - 1);
  }
  if (start > end || start >= size) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": hit.headers.get("Content-Type") || "audio/mpeg",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
      "Accept-Ranges": "bytes",
    },
  });
}
