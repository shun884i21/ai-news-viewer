// オフライン閲覧（PWA）：オンライン時は最新を取得し、失敗時のみキャッシュを使う
const CACHE = "ai-news-v5";
const ASSETS = ["./index.html", "./app.js", "./manifest.json", "./icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  // ネットワーク優先：最新を取りに行き、成功したらキャッシュ更新。失敗時はキャッシュへ。
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      // オフライン時：まず完全一致、無ければクエリ無視で探す（news.json?ts のキャッシュバスター対策）、
      // それも無ければ最後にindex.htmlを返す。
      .catch(() =>
        caches.match(e.request).then((hit) =>
          hit || caches.match(e.request, { ignoreSearch: true }).then((h2) => h2 || caches.match("./index.html"))
        )
      )
  );
});
