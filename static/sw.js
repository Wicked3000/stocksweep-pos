// Basic Service Worker to enable PWA Installation
self.addEventListener('install', (e) => {
  console.log('[Service Worker] Install');
});

self.addEventListener('fetch', (e) => {
  // We just let the browser handle requests normally for now
  e.respondWith(fetch(e.request));
});
