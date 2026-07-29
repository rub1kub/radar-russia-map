/*
 * Service worker карты: принимает push о тревоге или отбое по
 * отслеживаемым местам и показывает системное уведомление. Больше ничего
 * не делает — ни кеширования, ни перехвата запросов: карта живая, и
 * отдавать её из кеша значило бы показывать вчерашнюю обстановку.
 */

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    /* битый кадр — молчим */
  }
  if (!data.title) return;
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body || "",
      tag: data.tag || undefined,
      lang: "ru"
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => "focus" in w);
      return open ? open.focus() : self.clients.openWindow("/");
    })
  );
});
