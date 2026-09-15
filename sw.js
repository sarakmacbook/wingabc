const CACHE = 'khr-tracker-v1';
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/', '/manifest.json']))));
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
