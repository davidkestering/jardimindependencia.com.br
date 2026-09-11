// Service worker do interfone: recebe push e abre a página da chamada.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
self.addEventListener('push', e => {
  let d = {}; try { d = e.data.json(); } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.titulo || 'Interfone', {
    body: d.corpo || 'Chamada recebida', icon: '/static/img/icon-192.png', badge: '/static/img/icon-192.png',
    tag: d.tag || 'interfone', renotify: true, requireInteraction: (d.tag || 'interfone') === 'interfone', vibrate: [500, 300, 500, 300, 500], data: { url: d.url || '/morador/interfone' }
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data && e.notification.data.url || '/morador/interfone';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
    const c = cs.find(x => 'focus' in x); if (c) { c.navigate(url); return c.focus(); } return self.clients.openWindow(url);
  }));
});
