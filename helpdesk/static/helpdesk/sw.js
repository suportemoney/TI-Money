/* Service Worker — notificações Web Push do helpdesk */

const CACHE_VERSION = 'helpdesk-push-__HELPDESK_FRONTEND_VERSION__';
const ICON_URL = '/static/helpdesk/images/favicon.ico';

self.addEventListener('push', function(event) {
    if (!event.data) {
        return;
    }

    let payload = {};
    try {
        payload = event.data.json();
    } catch (e) {
        payload = { title: 'Helpdesk', body: event.data.text() };
    }

    const title = payload.title || 'Helpdesk';
    const options = {
        body: payload.body || '',
        icon: payload.icon || ICON_URL,
        badge: ICON_URL,
        tag: payload.tag || ('helpdesk-' + Date.now()),
        renotify: true,
        data: {
            url: payload.url || '/helpdesk/',
            tipo: payload.tipo || '',
        },
        requireInteraction: Boolean(payload.tipo === 'MENTION'),
    };

    event.waitUntil(
        Promise.all([
            self.registration.showNotification(title, options),
            // Avisa abas abertas para tocar áudio de menção (volume amplificado no cliente)
            clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
                if (payload.tipo !== 'MENTION') {
                    return;
                }
                // Interno ou chamado finalizado: não dispara som nas abas abertas
                if (payload.is_interno || String(payload.ticket_status || '').toUpperCase() === 'RESOLVED') {
                    return;
                }
                clientList.forEach(function(client) {
                    client.postMessage({
                        type: 'HELPDESK_MENTION_ALERT',
                        ticketUrl: payload.url || '/helpdesk/',
                        is_interno: Boolean(payload.is_interno),
                        ticket_status: payload.ticket_status || '',
                    });
                });
            }),
        ])
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();

    const url = (event.notification.data && event.notification.data.url) || '/helpdesk/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(clientList) {
            for (let i = 0; i < clientList.length; i++) {
                const client = clientList[i];
                if (client.url.indexOf('/helpdesk') !== -1 && 'focus' in client) {
                    client.navigate(url);
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(key) { return key.startsWith('helpdesk-push-') && key !== CACHE_VERSION; })
                    .map(function(key) { return caches.delete(key); })
            );
        })
    );
});
