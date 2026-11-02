// Shared committee laptops: log an idle voter out, warning them for the last 30 seconds.
// The server enforces the same limit (KioskMiddleware); this only makes it visible and keeps
// an active voter's session alive with a ping at most every 30 seconds.
(function () {
  var body = document.body;
  var limit = parseInt(body.dataset.idleSeconds, 10);
  var form = document.getElementById("logout-form");
  if (!limit || !form) return;
  var WARN = 30, PING_EVERY = 30000;
  var token = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  var last = Date.now(), lastPing = Date.now(), banner = null;

  function activity() {
    last = Date.now();
    if (banner) { banner.remove(); banner = null; }
    if (last - lastPing >= PING_EVERY) {
      lastPing = last;
      fetch(body.dataset.pingUrl, { method: "POST", headers: { "X-CSRFToken": token }, credentials: "same-origin" });
    }
  }

  function tick() {
    var left = limit - Math.floor((Date.now() - last) / 1000);
    if (left <= 0) { form.submit(); return; }
    if (left <= WARN) {
      if (!banner) {
        banner = document.createElement("p");
        banner.className = "idle-warning";
        banner.setAttribute("role", "alert");
        body.appendChild(banner);
      }
      banner.textContent = body.dataset.idleWarning.replace("%(seconds)s", left);
    }
    setTimeout(tick, 1000);
  }

  ["pointerdown", "keydown", "touchstart", "scroll"].forEach(function (name) {
    window.addEventListener(name, activity, { passive: true });
  });
  tick();
})();
