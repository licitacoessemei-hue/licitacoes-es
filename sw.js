/* Cache simples: tenta a rede primeiro e, sem internet, mostra a última versão vista. */
const CACHE = "licitacoes-es-v2";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const r = e.request;
  if (r.method !== "GET" || new URL(r.url).origin !== location.origin) return;
  e.respondWith(
    fetch(r).then(resp => {
      const copia = resp.clone();
      caches.open(CACHE).then(c => c.put(r, copia));
      return resp;
    }).catch(() => caches.match(r))
  );
});
