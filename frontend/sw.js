// Minimal service worker - required for PWA/TWA installability
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Pass-through fetch - no offline caching for now
  event.respondWith(fetch(event.request));
});