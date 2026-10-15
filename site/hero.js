/* Hero: real stock cards floating in 3D around the headline.
   - Cards burst out from a ball on load, slowly orbit and drift towards the viewer.
   - Play with them: cards move away from the cursor and lift when it's close,
     drag to spin the whole field (it keeps spinning, then slows down),
     click empty space to send a ripple that pushes every card outwards.
   - Scrolling down pulls the cards back into the centre.
   Usage: initHero(stocks.latest, stocks.series, summary.sector_today) once the JSON has loaded. */
(() => {
  const SECTORS = ["IT", "Banking", "Auto", "Pharma", "FMCG"];
  const CHIPS = ["20-day momentum", "RSI 14", "52-week high", "50 / 200-day average", "Volume spike", "Anomaly flag",
                 "Sector rotation", "Window functions", "Daily at 5 PM IST", "8 data checks"];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sign = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%");
  const rnd = (a, b) => a + Math.random() * (b - a);
  const sc = (s) => `var(--s-${String(s).toLowerCase()})`;
  const PERSP = 900, BASE_SPIN = 0.035;

  function spark(closes, up) {
    const c = (closes || []).filter((v) => v != null).slice(-40);
    if (c.length < 2) return "";
    const lo = Math.min(...c), hi = Math.max(...c), w = 120, h = 30;
    const pts = c.map((v, i) => `${((i / (c.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / (hi - lo || 1)) * h).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline class="${up ? "up" : "down"}" points="${pts}" fill="none" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  }

  window.initHero = function initHero(latest, series, sectorToday) {
    const hero = document.getElementById("hero3d");
    const field = document.getElementById("heroField");
    const glow = document.getElementById("heroGlow");
    if (!hero || !field || !latest || !latest.length) return;

    // ---------- build the cards ----------
    const items = [];
    latest.forEach((r) => {
      const up = (r.return_1d ?? 0) >= 0;
      const el = document.createElement("button");
      el.type = "button";
      el.className = "h-card";
      el.style.setProperty("--sc", sc(r.sector_name));
      el.setAttribute("aria-label", `Open ${r.company_name} in the stock explorer`);
      el.innerHTML = `<div class="h-top"><span class="h-tk">${esc(r.ticker.replace(".NS", ""))}</span><span class="h-sec">${esc(r.sector_name)}</span></div>
        <div class="h-name">${esc(r.company_name)}</div>
        ${spark(series && series[r.ticker] && series[r.ticker].c, up)}
        <div class="h-bot"><span class="h-px">₹${Number(r.close).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span><span class="h-ch ${up ? "up" : "down"}">${sign(r.return_1d)}</span></div>`;
      el.addEventListener("click", (e) => {
        if (dragged) { e.preventDefault(); return; }       // a drag that ended on a card is not a click
        const sel = document.getElementById("stockSelect");
        if (sel) { sel.value = r.ticker; sel.dispatchEvent(new Event("change")); }
        const target = document.getElementById("stocks");
        if (target) target.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
      });
      items.push({ el, kind: "card" });
    });
    CHIPS.forEach((t) => {
      const el = document.createElement("span");
      el.className = "h-chip";
      el.textContent = t;
      items.push({ el, kind: "chip" });
    });
    const fresh = (it) => { it.r = rnd(0.55, 1.05); };
    items.forEach((it, i) => {
      it.a = (i / items.length) * Math.PI * 2 + rnd(-0.18, 0.18);   // spread evenly so the centre stays clear
      fresh(it);
      it.z = rnd(-900, 300);
      it.v = rnd(18, 42);                          // forward drift, px per second
      it.rot = rnd(-7, 7);
      it.ox = 0; it.oy = 0; it.lift = 0;           // cursor push-away offset and hover lift
      field.appendChild(it.el);
    });

    // ---------- cycling sector word, with today's move ----------
    const word = document.getElementById("heroWord");
    const today = document.getElementById("heroToday");
    const tmap = {};
    (sectorToday || []).forEach((r) => (tmap[r.sector_name] = r.avg_return_1d));
    if (!Object.keys(tmap).length) {                // fallback: average the snapshot
      const g = {};
      latest.forEach((r) => r.return_1d != null && (g[r.sector_name] ||= []).push(r.return_1d));
      Object.entries(g).forEach(([k, v]) => (tmap[k] = v.reduce((a, x) => a + x, 0) / v.length));
    }
    let wi = 0;
    const showWord = () => {
      const s = SECTORS[wi % SECTORS.length], v = tmap[s];
      if (word) {
        word.classList.remove("in"); void word.offsetWidth;
        word.textContent = s; word.style.color = sc(s); word.classList.add("in");
      }
      if (today) today.innerHTML = v == null ? "" :
        `<span class="dot" style="background:${sc(s)}"></span>${esc(s)} today <b class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "▲" : "▼"} ${Math.abs(v * 100).toFixed(2)}%</b>`;
      wi++;
    };
    showWord();
    if (!reduce) setInterval(showWord, 2400);

    // ---------- size ----------
    let W = 0, H = 0, R = 0;
    const size = () => { W = hero.clientWidth; H = hero.clientHeight; R = Math.max(W, H) * 0.62; };
    size(); addEventListener("resize", size);

    // ---------- cursor: tilt, glow, push-away, drag to spin, click for a ripple ----------
    let mx = 0, my = 0, tx = 0, ty = 0;            // normalised -0.5..0.5 for the tilt
    let px = null, py = null;                      // cursor in px from the hero centre (null = outside)
    let spin = BASE_SPIN, down = null, dragged = false, lastX = 0, lastT = 0;
    let pulse = 0, pulseX = 0, pulseY = 0;
    const local = (e) => { const b = hero.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };

    hero.addEventListener("pointermove", (e) => {
      const [x, y] = local(e);
      mx = x / W - 0.5; my = y / H - 0.5; px = x - W / 2; py = y - H / 2;
      if (glow) { glow.style.transform = `translate(${x}px, ${y}px)`; glow.style.opacity = "1"; }
      if (down) {
        const dx = e.clientX - lastX, now = performance.now();
        if (Math.abs(e.clientX - down.x) > 6) { dragged = true; hero.classList.add("dragging"); }
        if (dragged) spin = (dx / Math.max(1, now - lastT)) * 1.6;    // px/ms → radians/s
        lastX = e.clientX; lastT = now;
      }
    });
    hero.addEventListener("pointerleave", () => {
      mx = 0; my = 0; px = null; py = null;
      if (glow) glow.style.opacity = "0";
    });
    hero.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".hero-copy a, .hero-copy button")) return;
      down = { x: e.clientX }; dragged = false; lastX = e.clientX; lastT = performance.now();
    });
    addEventListener("pointerup", (e) => {
      if (!down) return;
      hero.classList.remove("dragging");
      if (!dragged && !e.target.closest(".h-card, .hero-copy a, .hero-copy button") && hero.contains(e.target)) {
        const [x, y] = local(e);                   // click on empty space: ripple
        pulse = 1; pulseX = x - W / 2; pulseY = y - H / 2;
        const ring = document.createElement("span");
        ring.className = "h-ripple"; ring.style.left = x + "px"; ring.style.top = y + "px";
        hero.appendChild(ring); setTimeout(() => ring.remove(), 900);
      }
      down = null;
      setTimeout(() => (dragged = false), 0);
    });

    // ---------- animation loop ----------
    const t0 = performance.now();
    let last = t0, visible = true, raf = 0;
    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      document.body.classList.toggle("on-hero", e.intersectionRatio > 0.12);
      if (visible && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    }, { threshold: [0, 0.12, 0.3] }).observe(hero);

    const easeOut = (x) => 1 - Math.pow(1 - x, 4);
    function place(it, burst, collapse, dt) {
      const rr = it.r * R * burst * (1 - 0.82 * collapse) * (1 + 0.22 * pulse);
      let x = Math.cos(it.a) * rr, y = Math.sin(it.a) * rr * 0.62;
      const z = it.z * (0.25 + 0.75 * burst);
      const k = PERSP / (PERSP - z);               // how big it looks at this depth
      // push away from the cursor (worked out on screen, applied back in 3D)
      let tox = 0, toy = 0, tl = 0;
      if (px != null && !reduce) {
        const dx = x * k - px, dy = y * k - py, d = Math.hypot(dx, dy) || 1, near = 230;
        if (d < near) {
          const f = Math.pow(1 - d / near, 2);
          tox = (dx / d) * f * 140 / k; toy = (dy / d) * f * 140 / k;
          tl = f;
        }
      }
      const ease = Math.min(1, dt * 7);
      it.ox += (tox - it.ox) * ease; it.oy += (toy - it.oy) * ease; it.lift += (tl - it.lift) * ease;
      x += it.ox; y += it.oy;
      let o = Math.min(1, (z + 900) / 350) * Math.min(1, (360 - z) / 160) * (0.4 + 0.6 * Math.min(1, (z + 900) / 1100));
      o = Math.max(0, Math.min(1, o + it.lift * 0.5)) * (1 - collapse) * Math.min(1, burst * 1.6);
      it.el.style.transform = `translate(-50%,-50%) translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,${(z + it.lift * 60).toFixed(1)}px) rotate(${(it.rot * (1 - it.lift)).toFixed(2)}deg) scale(${(1 + it.lift * 0.12).toFixed(3)})`;
      it.el.style.opacity = o.toFixed(3);
      it.el.classList.toggle("hot", it.lift > 0.35);
      it.el.style.pointerEvents = o > 0.35 ? "auto" : "none";
    }
    function frame(now) {
      raf = 0;
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const burst = reduce ? 1 : easeOut(Math.min(1, (now - t0) / 1800));
      const collapse = Math.min(1, Math.max(0, -hero.getBoundingClientRect().top / (H * 0.9)));
      tx += (mx - tx) * 0.06; ty += (my - ty) * 0.06;
      if (!down) spin += (BASE_SPIN - spin) * Math.min(1, dt * 0.9);   // flicked spin slows back down
      pulse = Math.max(0, pulse - dt * 1.6);
      field.style.transform = `rotateX(${(-ty * 8).toFixed(2)}deg) rotateY(${(tx * 10).toFixed(2)}deg)`;
      items.forEach((it) => {
        if (!reduce) {
          it.a += dt * spin;
          it.z += dt * it.v;
          if (it.z > 360) { it.z = -900; fresh(it); }
        }
        place(it, burst, collapse, dt);
      });
      hero.style.setProperty("--collapse", collapse.toFixed(3));
      if (visible && !reduce) raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    if (reduce) addEventListener("scroll", () => requestAnimationFrame(frame), { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
      else if (visible && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    });
    hero.classList.add("ready");
  };

  // ---------- floating section dock: highlight the section you're reading ----------
  window.initDock = function initDock() {
    const dock = document.getElementById("dock");
    if (!dock) return;
    const links = [...dock.querySelectorAll("a[href^='#']")];
    const map = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (!e.isIntersecting) return;
      links.forEach((a) => a.classList.remove("on"));
      const a = map.get(e.target.id);
      if (a) {                                     // keep the active link visible when the dock scrolls (phones)
        a.classList.add("on");
        const inner = dock.querySelector(".dock-inner");
        if (inner && inner.scrollWidth > inner.clientWidth) inner.scrollTo({ left: a.offsetLeft - inner.clientWidth / 2 + a.offsetWidth / 2, behavior: "smooth" });
      }
    }), { rootMargin: "-45% 0px -50% 0px" });
    map.forEach((_, id) => { const el = document.getElementById(id); if (el) io.observe(el); });
  };

  // ---------- reveal sections as they scroll into view ----------
  window.initReveal = function initReveal() {
    const els = document.querySelectorAll("main .card, main .kpis");
    if (reduce || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("shown"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px" });
    els.forEach((el) => { el.classList.add("reveal"); io.observe(el); });
  };
})();
