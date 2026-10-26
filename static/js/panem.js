/* Painted backgrounds (p5.js, instance mode), the countdown ticker, and confirmation dialogs.
   Scenes are decorative: hosts are aria-hidden and the page works without this file.
   Every scene paints once (noLoop) and redraws only on resize, font load, or a slow timer. */
(function () {
  "use strict";

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var EMBER = [217, 138, 43], GOLD = [201, 163, 92], BONE = [231, 229, 228], INK = [12, 10, 9];
  var DISPLAY = "Cormorant Garamond"; // p5 quotes names containing spaces, so no fallback list

  function density(p) { p.pixelDensity(Math.min(window.devicePixelRatio || 1, 1.5)); }
  function size(host) { return [Math.max(1, host.clientWidth), Math.max(1, host.clientHeight)]; }

  function debounce(fn, ms) {
    var timer;
    return function () { window.clearTimeout(timer); timer = window.setTimeout(fn, ms); };
  }

  // Calls onChange(true|false) as the host enters or leaves the viewport.
  function watchVisibility(host, onChange) {
    if (!("IntersectionObserver" in window)) { onChange(true); return; }
    new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) { onChange(entry.isIntersecting); });
    }).observe(host);
  }

  function mount(host, sketch) {
    new window.p5(function (p) { sketch(p, host); }, host);
    var canvas = host.querySelector("canvas");
    if (canvas) canvas.setAttribute("aria-hidden", "true");
  }

  // Resize the canvas and repaint once, debounced.
  function repaintOnResize(p, host, paint) {
    p.windowResized = debounce(function () {
      var s = size(host);
      p.resizeCanvas(s[0], s[1]);
      paint();
      p.redraw();
    }, 150);
  }

  // ---------------------------------------------------------------- embers
  // Full-viewport night, painted once: warm ground glow, smoke, still sparks.
  function embers(p, host) {
    p.setup = function () {
      var s = size(host);
      density(p);
      p.createCanvas(s[0], s[1]);
      p.noLoop();
      repaintOnResize(p, host, function () {});
    };

    p.draw = function () {
      var ctx = p.drawingContext, w = p.width, h = p.height;
      ctx.fillStyle = "rgb(12,10,9)";
      ctx.fillRect(0, 0, w, h);
      var g = ctx.createRadialGradient(w * 0.5, h * 1.15, 0, w * 0.5, h * 1.15, Math.max(w, h) * 0.95);
      g.addColorStop(0, "rgba(120,52,16,0.5)");
      g.addColorStop(0.35, "rgba(52,26,12,0.3)");
      g.addColorStop(1, "rgba(12,10,9,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      // Smoke: one low-resolution noise pass, scaled up.
      var smoke = p.createGraphics(Math.ceil(w / 10), Math.ceil(h / 10));
      smoke.pixelDensity(1);
      smoke.loadPixels();
      for (var y = 0; y < smoke.height; y++) {
        for (var x = 0; x < smoke.width; x++) {
          var n = p.noise(x * 0.035, y * 0.05);
          var i = 4 * (y * smoke.width + x);
          smoke.pixels[i] = 70; smoke.pixels[i + 1] = 48; smoke.pixels[i + 2] = 34;
          smoke.pixels[i + 3] = Math.max(0, n - 0.45) * 2.2 * (0.35 + 0.65 * y / smoke.height) * 120;
        }
      }
      smoke.updatePixels();
      p.image(smoke, 0, 0, w, h);
      smoke.remove();

      // Still sparks, denser near the ground.
      p.randomSeed(3);
      p.noStroke();
      var count = w < 640 ? 40 : 90;
      for (var k = 0; k < count; k++) {
        var sx = p.random(w), sy = h * (1 - Math.pow(p.random(), 1.8));
        var fade = 0.25 + 0.75 * (sy / h), r = p.random(0.8, 2.2);
        var c = p.random() > 0.85 ? BONE : EMBER;
        p.fill(c[0], c[1], c[2], 30 * fade);
        p.circle(sx, sy, r * 6);
        p.fill(c[0], c[1], c[2], 200 * fade);
        p.circle(sx, sy, r);
      }
    };
  }

  // ----------------------------------------------------------------- arena
  // Top-down clock arena: 12 sectors around the horn. The lit arc is the real
  // share of the voting window that has elapsed; each sector is 1/12 of it.
  // Painted once; a small DOM tip marks "now" and is the only moving part.
  function arena(p, host) {
    var opens = Date.parse(host.dataset.opens || ""), closes = Date.parse(host.dataset.closes || "");
    var state = host.dataset.state || "idle";
    var tip = null, timer = null;

    function progress() {
      if (state === "closed") return 1;
      if (isNaN(opens) || isNaN(closes) || state === "draft" || state === "scheduled" || state === "idle") return 0;
      return Math.min(1, Math.max(0, (Date.now() - opens) / (closes - opens)));
    }

    function wobblyRing(r, amp, seed, steps) {
      p.beginShape();
      for (var i = 0; i <= steps; i++) {
        var a = (i / steps) * p.TWO_PI;
        var rr = r + (p.noise(seed + Math.cos(a) * 1.5, seed + Math.sin(a) * 1.5) - 0.5) * amp;
        p.vertex(Math.cos(a) * rr, Math.sin(a) * rr);
      }
      p.endShape();
    }

    function paintHorn(R) {
      var L = R * 0.2, ctx = p.drawingContext;
      p.push();
      p.rotate(-0.5);
      var grad = ctx.createLinearGradient(-L, 0, L, 0);
      grad.addColorStop(0, "rgba(120,88,40,0.9)");
      grad.addColorStop(1, "rgba(214,176,98,0.95)");
      p.fill(0);
      p.stroke(BONE[0], BONE[1], BONE[2], 120);
      p.strokeWeight(0.8);
      ctx.fillStyle = grad;
      p.beginShape();
      p.vertex(L, -L * 0.42);
      p.bezierVertex(L * 0.2, -L * 0.36, -L * 0.6, -L * 0.3, -L * 0.95, L * 0.05);
      p.bezierVertex(-L * 1.05, L * 0.2, -L * 0.9, L * 0.32, -L * 0.75, L * 0.18);
      p.bezierVertex(-L * 0.4, L * 0.1, L * 0.2, L * 0.36, L, L * 0.42);
      p.endShape(p.CLOSE);
      p.noFill();
      for (var i = 1; i < 6; i++) {
        var hh = L * (0.42 - i * 0.055);
        p.stroke(INK[0], INK[1], INK[2], 90);
        p.arc(L - i * L * 0.3, 0, hh * 0.5, hh * 2, -p.HALF_PI, p.HALF_PI);
      }
      p.fill(INK[0], INK[1], INK[2], 230);
      p.stroke(BONE[0], BONE[1], BONE[2], 140);
      p.ellipse(L, 0, L * 0.3, L * 0.84);
      p.pop();
    }

    function placeTip(R, prog) {
      if (!tip) return;
      var live = prog > 0 && prog < 1;
      tip.hidden = !live;
      if (!live) return;
      // rotate() runs clockwise from 12 o'clock, matching the arc.
      tip.style.transform = "rotate(" + (prog * 360) + "deg) translateY(" + (-R * 0.94) + "px)";
    }

    p.setup = function () {
      var s = size(host);
      density(p);
      p.createCanvas(s[0], s[1]);
      p.noLoop();
      if (state === "open" || state === "paused") {
        tip = document.createElement("span");
        tip.className = "arena-tip";
        tip.setAttribute("aria-hidden", "true");
        host.appendChild(tip);
      }
      repaintOnResize(p, host, function () {});
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { p.redraw(); });
      watchVisibility(host, function (visible) {
        host.classList.toggle("is-offscreen", !visible);
        window.clearInterval(timer);
        // The arc advances 1/12 every six hours; a five-minute repaint is plenty.
        if (visible && tip) timer = window.setInterval(function () { p.redraw(); }, 5 * 60 * 1000);
      });
    };

    p.draw = function () {
      var d = Math.min(p.width, p.height), R = d * 0.46, ctx = p.drawingContext;
      p.clear();
      p.push();
      p.translate(p.width / 2, p.height / 2);

      // Ground disc with a soft dark core.
      var disc = ctx.createRadialGradient(0, 0, R * 0.1, 0, 0, R * 1.04);
      disc.addColorStop(0, "rgba(40,30,22,0.95)");
      disc.addColorStop(0.7, "rgba(22,18,15,0.92)");
      disc.addColorStop(1, "rgba(12,10,9,0)");
      ctx.fillStyle = disc;
      ctx.beginPath(); ctx.arc(0, 0, R * 1.04, 0, Math.PI * 2); ctx.fill();

      // Jungle band: noise-thinned stipple between the beach and the rim.
      p.noStroke();
      p.randomSeed(7);
      var dots = Math.floor(R * R * 0.09);
      for (var i = 0; i < dots; i++) {
        var a = p.random(p.TWO_PI), r = Math.sqrt(p.random(0.36, 0.9)) * R;
        var x = Math.cos(a) * r, y = Math.sin(a) * r;
        var n = p.noise(x * 0.012 + 40, y * 0.012 + 40);
        if (n < 0.42) continue;
        p.fill(78 + n * 40, 72 + n * 30, 48, 40 + n * 70);
        p.circle(x, y, p.random(0.6, 1.8) * (d / 700 + 0.5));
      }

      // Beach and water rings.
      p.noFill();
      p.strokeWeight(1);
      p.stroke(GOLD[0], GOLD[1], GOLD[2], 40);
      wobblyRing(R * 0.6, R * 0.03, 3, 240);
      p.stroke(GOLD[0], GOLD[1], GOLD[2], 22);
      wobblyRing(R * 0.36, R * 0.02, 9, 240);
      for (var w = 0; w < 4; w++) {
        p.stroke(GOLD[0], GOLD[1], GOLD[2], 14);
        p.circle(0, 0, R * 2 * (0.4 + w * 0.045));
      }

      // Twelve spokes and the rim.
      p.strokeWeight(0.8);
      p.stroke(GOLD[0], GOLD[1], GOLD[2], 70);
      for (var k = 0; k < 12; k++) {
        var ang = -p.HALF_PI + k * p.TWO_PI / 12;
        p.line(Math.cos(ang) * R * 0.6, Math.sin(ang) * R * 0.6, Math.cos(ang) * R * 0.94, Math.sin(ang) * R * 0.94);
      }
      p.strokeWeight(1);
      p.stroke(GOLD[0], GOLD[1], GOLD[2], 110);
      p.circle(0, 0, R * 1.88);
      p.stroke(GOLD[0], GOLD[1], GOLD[2], 40);
      p.circle(0, 0, R * 1.98);

      // Rim ticks: 72 hours.
      for (var h = 0; h < 72; h++) {
        var ta = -p.HALF_PI + h * p.TWO_PI / 72;
        var len = h % 6 === 0 ? R * 0.035 : R * 0.014;
        p.stroke(GOLD[0], GOLD[1], GOLD[2], h % 6 === 0 ? 150 : 60);
        p.line(Math.cos(ta) * R * 0.94, Math.sin(ta) * R * 0.94, Math.cos(ta) * (R * 0.94 - len), Math.sin(ta) * (R * 0.94 - len));
      }

      // Sector numerals.
      p.noStroke();
      p.fill(GOLD[0], GOLD[1], GOLD[2], 150);
      p.textFont(DISPLAY);
      p.textAlign(p.CENTER, p.CENTER);
      p.textSize(Math.max(10, R * 0.045));
      var numerals = ["XII", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"];
      for (var s = 0; s < 12; s++) {
        var na = -p.HALF_PI + (s + 0.5) * p.TWO_PI / 12;
        p.text(numerals[s], Math.cos(na) * R * 1.07, Math.sin(na) * R * 1.07);
      }

      // Launch plates around the horn.
      p.strokeWeight(0.8);
      for (var q = 0; q < 24; q++) {
        var pa = q * p.TWO_PI / 24;
        p.fill(INK[0], INK[1], INK[2]);
        p.stroke(GOLD[0], GOLD[1], GOLD[2], 120);
        p.circle(Math.cos(pa) * R * 0.27, Math.sin(pa) * R * 0.27, R * 0.022);
      }
      paintHorn(R);

      // Elapsed window: current sector band, then the burnt arc.
      var prog = progress(), start = -p.HALF_PI;
      p.noFill();
      if (state === "closed") {
        p.stroke(GOLD[0], GOLD[1], GOLD[2], 150);
        p.strokeWeight(2);
        p.circle(0, 0, R * 1.88);
      } else if (prog > 0) {
        var sector = Math.min(11, Math.floor(prog * 12));
        p.stroke(EMBER[0], EMBER[1], EMBER[2], 22);
        p.strokeWeight(R * 0.3);
        p.strokeCap(p.SQUARE);
        p.arc(0, 0, R * 1.56, R * 1.56, start + sector * p.TWO_PI / 12, start + (sector + 1) * p.TWO_PI / 12);
        p.strokeCap(p.ROUND);
        p.stroke(EMBER[0], EMBER[1], EMBER[2], state === "paused" ? 140 : 255);
        p.strokeWeight(2.4);
        p.arc(0, 0, R * 1.88, R * 1.88, start, start + prog * p.TWO_PI);
      }
      p.pop();
      placeTip(R, prog);
    };
  }

  // ------------------------------------------------------------------ seal
  // One-shot (about 1.2s) when it scrolls into view: sparks converge,
  // twelve notches burn in, the mark settles. Then it holds still.
  function seal(p, host) {
    var sparks = [], start = null, DURATION = 1200;
    var mark = host.dataset.mark || "";

    function ease(x) { return 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3); }

    p.setup = function () {
      var s = size(host);
      density(p);
      p.createCanvas(s[0], s[1]);
      p.noLoop();
      for (var i = 0; i < 70; i++) sparks.push({ a: p.random(p.TWO_PI), r0: p.random(0.75, 1.1), delay: p.random(0, 0.35), size: p.random(1, 2.6) });
      if (reduced) { start = -DURATION; return; }
      watchVisibility(host, function (visible) {
        if (visible && start === null) { start = p.millis(); p.loop(); }
      });
    };

    p.draw = function () {
      var k = start === null ? 0 : Math.min(1, (p.millis() - start) / DURATION);
      if (start !== null && start < 0) k = 1;
      var R = Math.min(p.width, p.height) * 0.38;
      p.clear();
      p.translate(p.width / 2, p.height / 2);

      p.noStroke();
      sparks.forEach(function (s) {
        var local = ease((k - s.delay) / 0.45);
        if (local <= 0 || local >= 1) return;
        var r = p.lerp(s.r0 * p.width * 0.5, R, local);
        p.fill(EMBER[0], EMBER[1], EMBER[2], 200 * (1 - local));
        p.circle(Math.cos(s.a) * r, Math.sin(s.a) * r, s.size);
      });

      var burn = ease((k - 0.25) / 0.45), cool = ease((k - 0.6) / 0.4);
      p.noFill();
      p.stroke(p.lerp(EMBER[0], GOLD[0], cool), p.lerp(EMBER[1], GOLD[1], cool), p.lerp(EMBER[2], GOLD[2], cool));
      if (burn > 0) {
        p.strokeWeight(1.4);
        p.arc(0, 0, R * 2, R * 2, -p.HALF_PI, -p.HALF_PI + burn * p.TWO_PI);
        p.strokeWeight(0.8);
        p.arc(0, 0, R * 1.72, R * 1.72, -p.HALF_PI, -p.HALF_PI + burn * p.TWO_PI);
      }
      p.strokeWeight(1.2);
      for (var i = 0; i < 12 && i / 12 <= burn; i++) {
        var a = -p.HALF_PI + i * p.TWO_PI / 12;
        p.line(Math.cos(a) * R * 0.86, Math.sin(a) * R * 0.86, Math.cos(a) * R, Math.sin(a) * R);
      }

      var show = ease((k - 0.7) / 0.3);
      if (show > 0 && mark) {
        p.noStroke();
        p.fill(BONE[0], BONE[1], BONE[2], 255 * show);
        p.textFont(DISPLAY);
        p.textAlign(p.CENTER, p.CENTER);
        p.textSize(R * 0.9);
        p.text(mark, 0, -R * 0.06);
      }
      if (k >= 1) p.noLoop();
    };
  }

  // ------------------------------------------------------------- countdown
  var ticker = null;
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function tick() {
    var now = Date.now();
    document.querySelectorAll("[data-countdown]").forEach(function (el) {
      var target = Date.parse(el.dataset.countdown);
      if (isNaN(target)) return;
      var left = Math.max(0, Math.floor((target - now) / 1000));
      el.textContent = pad(Math.floor(left / 3600)) + ":" + pad(Math.floor(left % 3600 / 60)) + ":" + pad(left % 60);
    });
  }

  function runTicker() {
    window.clearInterval(ticker);
    if (document.hidden || !document.querySelector("[data-countdown]")) return;
    tick();
    ticker = window.setInterval(tick, 1000);
  }

  // ------------------------------------------------------- confirm dialogs
  // Buttons with data-confirm="<dialog id>" open a native modal dialog (focus trap,
  // Escape, inert page) instead of submitting. Its confirm button submits the
  // original form with the same name/value. Without JS the button submits directly.
  function wireConfirms() {
    document.querySelectorAll("[data-confirm]").forEach(function (trigger) {
      var dialog = document.getElementById(trigger.dataset.confirm);
      if (!dialog || typeof dialog.showModal !== "function") return;
      trigger.addEventListener("click", function (event) {
        event.preventDefault();
        var submit = dialog.querySelector("[data-confirm-submit]");
        submit.setAttribute("form", trigger.form.id);
        submit.name = trigger.name;
        submit.value = trigger.value;
        dialog.querySelectorAll("[data-confirm-copy]").forEach(function (el) {
          el.textContent = trigger.dataset[el.dataset.confirmCopy] || "";
        });
        dialog.returnFocus = trigger;
        dialog.showModal();
        dialog.querySelector("[data-confirm-cancel]").focus();
      });
    });
    document.querySelectorAll("dialog.confirm").forEach(function (dialog) {
      dialog.querySelector("[data-confirm-cancel]").addEventListener("click", function () { dialog.close(); });
      dialog.addEventListener("close", function () { if (dialog.returnFocus) dialog.returnFocus.focus(); });
    });
  }

  function boot() {
    runTicker();
    document.addEventListener("visibilitychange", runTicker);
    wireConfirms();
    if (!window.p5) return;
    var scenes = { embers: embers, arena: arena, seal: seal };
    document.querySelectorAll("[data-scene]").forEach(function (host) {
      var sketch = scenes[host.dataset.scene];
      if (sketch) mount(host, sketch);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
