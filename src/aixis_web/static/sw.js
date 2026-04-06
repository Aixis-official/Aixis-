// Aixis Service Worker — network-first navigation, cache-first static assets
const CACHE_NAME = 'aixis-static-v2';
const OFFLINE_URL = '/offline';
const PRE_CACHE_URLS = [
  OFFLINE_URL,
  '/static/img/Aixis-logo-final.png',
];

// Install: pre-cache offline page and key assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRE_CACHE_URLS))
  );
  self.skipWaiting();
});

// Activate: purge old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch handler
self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Only handle GET requests
  if (request.method !== 'GET') return;

  // Navigation requests — network-first, offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  // Static assets (CSS, JS, fonts, images) — cache-first
  const url = new URL(request.url);
  const isStatic =
    url.pathname.startsWith('/static/') ||
    /\.(css|js|woff2?|ttf|eot|png|jpe?g|gif|svg|webp|ico)$/i.test(url.pathname);

  if (isStatic) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            // Cache successful responses
            if (response.ok) {
              const clone = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
            }
            return response;
          })
      )
    );
    return;
  }

  // All other requests — network only
  event.respondWith(fetch(request));
});
