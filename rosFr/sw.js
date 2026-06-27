const CACHE_NAME = 'sala-virtual-v2';
// Recursos estáticos que se guardarán en caché durante la instalación
const urlsToCache = [
  '/',
  '/principal.html',
  '/login.html',
  '/sala.html',
  '/manifest.json',
  '/sw.js'
];

/**
 * Evento de instalación: Se dispara la primera vez que el navegador registra el Service Worker.
 * Aquí pre-cacheamos los recursos definidos en `urlsToCache`.
 */
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('[Service Worker] Precaching recursos de la App');
        return cache.addAll(urlsToCache);
      })
  );
});

/**
 * Evento fetch: Intercepta todas las peticiones de red.
 * Implementa la estrategia "Network First":
 * Intentará obtener el recurso de la red primero, y si no hay conexión, recurrirá a la caché.
 */
self.addEventListener('fetch', event => {
  // Solo interceptar peticiones de nuestro propio origen para HTML, JS, CSS, etc.
  if (event.request.url.startsWith(self.location.origin)) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Si la respuesta es válida, clonarla y guardarla en cache
          if (response && response.status === 200) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(event.request, responseToCache);
            });
          }
          return response;
        })
        .catch(() => {
          // Si falla la red (offline), buscar en caché
          return caches.match(event.request);
        })
    );
  } else {
    // Para peticiones externas (Cloudflare RealtimeKit, CDN, etc.), consultar directo de red
    event.respondWith(fetch(event.request));
  }
});

/**
 * Evento de activación: Se dispara después de que el SW se instala y la página se recarga.
 * Es útil para limpiar cachés de versiones anteriores de la aplicación.
 */
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            console.log('[Service Worker] Eliminando caché antigua:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});
