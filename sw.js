// RouteBook service worker — auto-generated, do not edit
const VERSION = '20260415203215';
const CACHE   = 'routebook-' + VERSION;

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(c) {
      return c.addAll(['./viewer.html', './sw.js']);
    }).then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE; })
            .map(function(k) { return caches.delete(k); })
      );
    }).then(function() { return self.clients.claim(); })
    .then(function() {
      // Notify all open tabs that a new version is available
      self.clients.matchAll({ type: 'window' }).then(function(clients) {
        clients.forEach(function(c) { c.postMessage({ type: 'UPDATE_AVAILABLE', version: VERSION }); });
      });
    })
  );
});

self.addEventListener('fetch', function(e) {
  // Network-first for viewer.html and sw.js so updates are always picked up
  if (e.request.url.endsWith('viewer.html') || e.request.url.endsWith('sw.js')) {
    e.respondWith(
      fetch(e.request).then(function(resp) {
        return caches.open(CACHE).then(function(c) {
          c.put(e.request, resp.clone());
          return resp;
        });
      }).catch(function() { return caches.match(e.request); })
    );
    return;
  }
  // Cache-first for everything else (tiles etc)
  e.respondWith(
    caches.match(e.request).then(function(r) {
      return r || fetch(e.request);
    })
  );
});
