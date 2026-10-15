/* Hero: real stock cards floating in 3D around the headline.
   Cards burst out from a ball on load, drift slowly around the centre and towards the viewer,
   tilt with the mouse, and pull back into the centre as you scroll down.
   Usage: initHero(stocks.latest, stocks.series, sectorToday) once the JSON has loaded. */
(() => {
  const SECTORS = ["IT", "Banking", "Auto", "Pharma", "FMCG"];
  const SECTOR_COLOR = { IT: "#3987e5", Banking: "#d95926", Auto: "#199e70", Pharma: "#e0a100", FMCG: "#d55181" };
  const CHIPS = ["20-day momentum", "RSI 14", "52-week high", "50 / 200-day average", "Volume spike", "Anomaly flag",
                 "Sector rotation", "Window functions", "Daily at 5 PM IST", "8 data checks"];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sign = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(2) + "%");
  const rnd = (a, b) => a + Math.random() * (b - a);

  function spark(closes, up) {
    const c = (closes || []).filter((v) => v != null).slice(-40);
    if (c.length < 2) return "";
    const lo = Math.min(...c), hi = Math.max(...c), w = 120, h = 30;
    const pts = c.map((v, i) => `${((i / (c.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / (hi - lo || 1)) * h).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="${up ? "#34c26b" : "#ef6b6b"}" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  }

  window.initHero = function initHero(latest, series, sectorToday) {
    const hero = document.getElementById("hero3d");
    const field = document.getElementById("heroField");
    if (!hero || !field || !latest || !latest.length) return;

    // ---------- build the cards ----------
    const items = [];
    latest.forEach((r) => {
      const up = (r.return_1d ?? 0) >= 0;
      const el = document.createElement("button");
      el.type = "button";
      el.className = "h-card";
      el.style.setProperty("--sc", SECTOR_COLOR[r.sector_name] || "#888");
      el.setAttribute("aria-label", `Open ${r.company_name} in the stock explorer`);
      el.innerHTML = `<div class="h-top"><span class="h-tk">${esc(r.ticker.replace(".NS", ""))}</span><span class="h-sec">${esc(r.sector_name)}</span></div>
        <div class="h-name">${esc(r.company_name)}</div>
        ${spark(series && series[r.ticker] && series[r.ticker].c, up)}
        <div class="h-bot"><span class="h-px">₹${Number(r.close).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</span><span class="h-ch ${up ? "up" : "down"}">${sign(r.return_1d)}</span></div>`;
      el.addEventListener("click", () => {
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
    items.forEach((it, i) => {
      // spread evenly around the circle so the centre stays clear for the title
      it.a = (i / items.length) * Math.PI * 2 + rnd(-0.18, 0.18);
      it.r = rnd(0.55, 1.05);                      // fraction of the max radius
      it.z = rnd(-900, 300);
      it.v = rnd(18, 42);                          // forward drift, px per second
      it.rot = rnd(-7, 7);
      it.el.style.setProperty("--d", (i * 0.025).toFixed(3) + "s");
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
        word.textContent = s; word.style.color = SECTOR_COLOR[s]; word.classList.add("in");
      }
      if (today) today.innerHTML = v == null ? "" :
        `<span class="dot" style="background:${SECTOR_COLOR[s]}"></span>${esc(s)} today <b class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "▲" : "▼"} ${Math.abs(v * 100).toFixed(2)}%</b>`;
      wi++;
    };
    showWord();
    const wordTimer = reduce ? null : setInterval(showWord, 2400);

    // ---------- animation loop ----------
    let W = 0, H = 0, R = 0;
    const size = () => { W = hero.clientWidth; H = hero.clientHeight; R = Math.max(W, H) * 0.62; };
    size(); addEventListener("resize", size);

    let mx = 0, my = 0, tx = 0, ty = 0;
    hero.addEventListener("pointermove", (e) => {
      const b = hero.getBoundingClientRect();
      mx = (e.clientX - b.left) / b.width - 0.5; my = (e.clientY - b.top) / b.height - 0.5;
    });
    hero.addEventListener("pointerleave", () => { mx = 0; my = 0; });

    const t0 = performance.now();
    let last = t0, visible = true, raf = 0;
    new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      document.body.classList.toggle("on-hero", e.intersectionRatio > 0.12);
      if (visible && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    }, { threshold: [0, 0.12, 0.3] }).observe(hero);

    const easeOut = (x) => 1 - Math.pow(1 - x, 4);
    function place(it, burst, collapse) {
      const rr = it.r * R * burst * (1 - 0.82 * collapse);
      const x = Math.cos(it.a) * rr, y = Math.sin(it.a) * rr * 0.62;
      const z = it.z * (0.25 + 0.75 * burst);
      // fade in from far away, fade out just before reaching the viewer
      let o = Math.min(1, (z + 900) / 350) * Math.min(1, (360 - z) / 160) * (0.4 + 0.6 * Math.min(1, (z + 900) / 1100));
      o = Math.max(0, o) * (1 - collapse) * Math.min(1, burst * 1.6);
      it.el.style.transform = `translate(-50%,-50%) translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,${z.toFixed(1)}px) rotate(${it.rot}deg)`;
      it.el.style.opacity = o.toFixed(3);
      it.el.style.pointerEvents = o > 0.35 ? "auto" : "none";
    }
    function frame(now) {
      raf = 0;
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const burst = reduce ? 1 : easeOut(Math.min(1, (now - t0) / 1800));
      const collapse = Math.min(1, Math.max(0, -hero.getBoundingClientRect().top / (H * 0.9)));
      tx += (mx - tx) * 0.06; ty += (my - ty) * 0.06;
      field.style.transform = `rotateX(${(-ty * 8).toFixed(2)}deg) rotateY(${(tx * 10).toFixed(2)}deg)`;
      items.forEach((it) => {
        if (!reduce) {
          it.a += dt * 0.035;                      // slow orbit
          it.z += dt * it.v;                       // drift towards the viewer
          if (it.z > 360) { it.z = -900; it.r = rnd(0.55, 1.05); }
        }
        place(it, burst, collapse);
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
