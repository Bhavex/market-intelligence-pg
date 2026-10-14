/* NSE Sector Intelligence — reads static JSON exported by src/export_site.py */
(() => {
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const SECTORS = ["IT", "Banking", "Auto", "Pharma", "FMCG"];          // fixed colour order
  const color = (s) => css("--s-" + s.toLowerCase()) || "#888";
  const pct = (v, d = 1) => (v == null ? "–" : (v * 100).toFixed(d) + "%");
  const sign = (v, d = 1) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%");
  const cls = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
  const num = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtDate = (s) => (s ? new Date(s + (s.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "–");

  // diverging scale: red (neg) <- neutral -> blue (pos)
  const themeAttr = document.documentElement.dataset.theme;
  const dark = themeAttr ? themeAttr === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const NEU = dark ? [56, 56, 53] : [240, 239, 236];
  const POS = dark ? [57, 135, 229] : [28, 92, 171];
  const NEG = dark ? [230, 103, 103] : [180, 44, 44];
  const mix = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
  const diverge = (v, max) => {
    if (v == null) return { bg: "transparent", fg: css("--muted") };
    const t = Math.min(Math.abs(v) / max, 1);
    const rgb = mix(NEU, v >= 0 ? POS : NEG, t);
    return { bg: `rgb(${rgb})`, fg: !dark && t > 0.55 ? "#fff" : css("--ink") };
  };

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.plugins.tooltip.backgroundColor = dark ? "#2c2c2a" : "#0b0b0b";
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 4;
  const lineInteraction = { mode: "index", intersect: false };
  // x-axis that labels each year once, at its first data point
  const yearAxis = (labels) => ({
    grid: { display: false },
    ticks: { autoSkip: false, maxRotation: 0,
             callback: (v, i) => (i === 0 || labels[i].slice(0, 4) !== labels[i - 1].slice(0, 4) ? labels[i].slice(0, 4) : null) },
  });

  const load = (f) => fetch("data/" + f + "?v=" + Date.now()).then((r) => r.json());

  Promise.all(["summary", "sectors", "stocks", "anomalies", "backtest"].map((f) => load(f + ".json")))
    .then(([summary, sectors, stocks, anomalies, backtest]) => {
      document.body.classList.remove("loading");
      renderSummary(summary);
      renderFacts(summary, sectors);
      renderStrength(sectors.strength);
      renderSectorChart(sectors);
      renderYears(sectors.yearly);
      renderStocks(stocks);
      renderMap(stocks.latest);
      renderAnomalies(anomalies);
      renderBacktest(backtest);
      renderPipeline(summary.pipeline);
    })
    .catch((e) => {
      document.body.classList.remove("loading");
      document.getElementById("updated").textContent = "Could not load data. Please refresh.";
      console.error(e);
    });

  function renderSummary(s) {
    document.getElementById("updated").textContent = "Data as of " + fmtDate(s.latest_date);
    document.getElementById("lastUpdated").textContent = "Last updated: " +
      new Date(s.generated_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) + " IST";
    const k = [
      ["Latest trading day", fmtDate(s.latest_date), "end-of-day prices"],
      ["Stocks in uptrend", `${s.n_up} / ${s.stocks}`, "price > 50-day > 200-day avg"],
      ["Stocks in downtrend", `${s.n_down} / ${s.stocks}`, "price < 50-day < 200-day avg"],
      ["Anomalies today", s.n_anomalies, "unusual price or volume"],
      ["Rows in database", (s.price_rows + s.signal_rows).toLocaleString("en-IN"), "prices + signals since " + new Date(s.first_date).getFullYear()],
      ["Days run automatically", s.automated_days, "GitHub Actions, weekdays"],
    ];
    document.getElementById("kpis").innerHTML = k
      .map(([l, v, h]) => `<div class="kpi"><div class="label">${l}</div><div class="value">${esc(v)}</div><div class="hint">${h}</div></div>`)
      .join("");
  }

  function renderStrength(rows) {
    new Chart(document.getElementById("strengthChart"), {
      type: "bar",
      data: {
        labels: rows.map((r) => r.sector_name),
        datasets: [{
          data: rows.map((r) => r.avg_momentum_20d * 100),
          backgroundColor: rows.map((r) => color(r.sector_name)),
          borderRadius: 4, borderSkipped: "start", barThickness: 22,
        }],
      },
      options: {
        indexAxis: "y", maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => " 20-day momentum: " + c.raw.toFixed(2) + "%" } } },
        scales: { x: { ticks: { callback: (v) => v + "%" }, grid: { color: css("--grid") } }, y: { grid: { display: false } } },
      },
    });
    document.getElementById("strengthTable").innerHTML =
      `<tr><th>#</th><th>Sector</th><th class="num">Momentum</th><th class="num">vs market</th><th class="num">Volatility</th><th class="num">Up / Down</th></tr>` +
      rows.map((r) => `<tr><td>${r.strength_rank}</td>
        <td><span class="swatch" style="background:${color(r.sector_name)}"></span>${esc(r.sector_name)}</td>
        <td class="num ${cls(r.avg_momentum_20d)}">${sign(r.avg_momentum_20d, 2)}</td>
        <td class="num ${cls(r.relative_strength)}">${sign(r.relative_strength, 2)}</td>
        <td class="num">${pct(r.avg_volatility_20d, 0)}</td>
        <td class="num">${r.n_uptrend} / ${r.n_downtrend}</td></tr>`).join("");
  }

  function renderSectorChart(s) {
    new Chart(document.getElementById("sectorChart"), {
      type: "line",
      data: {
        labels: s.dates,
        datasets: SECTORS.filter((k) => s.index[k]).map((k) => ({
          label: k, data: s.index[k], borderColor: color(k), backgroundColor: color(k),
        })),
      },
      options: {
        maintainAspectRatio: false, interaction: lineInteraction,
        plugins: { tooltip: { callbacks: { title: (i) => fmtDate(i[0].label), label: (c) => ` ${c.dataset.label}: ${c.raw?.toFixed(1)}` } } },
        scales: {
          x: yearAxis(s.dates),
          y: { grid: { color: css("--grid") } },
        },
      },
    });
  }

  function renderYears(y) {
    const max = Math.max(0.3, ...Object.values(y.returns).flat().filter((v) => v != null).map(Math.abs));
    const head = `<tr><th>Sector</th>${y.years.map((yr) => `<th class="num">${yr}${yr === new Date().getFullYear() ? " YTD" : ""}</th>`).join("")}</tr>`;
    const body = SECTORS.filter((s) => y.returns[s]).map((s) => `<tr><td><span class="swatch" style="background:${color(s)}"></span>${s}</td>${
      y.returns[s].map((v) => { const c = diverge(v, max); return `<td class="num" style="background:${c.bg};color:${c.fg}">${sign(v, 0)}</td>`; }).join("")}</tr>`).join("");
    document.getElementById("yearTable").innerHTML = head + body;
  }

  function renderStocks(data) {
    const sel = document.getElementById("stockSelect");
    const byTicker = Object.fromEntries(data.latest.map((r) => [r.ticker, r]));
    sel.innerHTML = SECTORS.map((s) => `<optgroup label="${s}">${
      data.latest.filter((r) => r.sector_name === s).map((r) => `<option value="${esc(r.ticker)}">${esc(r.company_name)} (${esc(r.ticker.replace(".NS", ""))})</option>`).join("")}</optgroup>`).join("");
    const fromHash = decodeURIComponent((location.hash.match(/stock=([^&]+)/) || [])[1] || "");
    sel.value = byTicker[fromHash] ? fromHash : byTicker["INFY.NS"] ? "INFY.NS" : data.latest[0].ticker;
    const label = (r) => `${r.company_name} (${r.ticker.replace(".NS", "")})`;
    document.getElementById("stockList").innerHTML = data.latest.map((r) => `<option value="${esc(label(r))}"></option>`).join("");
    const search = document.getElementById("stockSearch");
    search.addEventListener("change", () => {
      const q = search.value.trim().toLowerCase();
      if (!q) return;
      const hit = data.latest.find((r) => label(r).toLowerCase() === q) ||
                  data.latest.find((r) => label(r).toLowerCase().includes(q));
      if (hit) { sel.value = hit.ticker; draw(true); search.value = ""; }
      else toast("No stock matches “" + search.value + "”");
    });
    document.getElementById("copyLink").addEventListener("click", () =>
      copyText(location.origin + location.pathname + "#stock=" + encodeURIComponent(sel.value), "Link to this stock copied"));
    let price, rsi;
    const draw = (fromUser) => {
      const t = sel.value, s = data.series[t], r = byTicker[t];
      if (fromUser === true) history.replaceState(null, "", "#stock=" + encodeURIComponent(t));
      document.getElementById("stockStats").innerHTML = [
        ["Close", "₹" + num(r.close)],
        ["1 day", `<span class="${cls(r.return_1d)}">${sign(r.return_1d, 2)}</span>`],
        ["1 month", `<span class="${cls(r.momentum_20d)}">${sign(r.momentum_20d)}</span>`],
        ["1 year", `<span class="${cls(r.return_1y)}">${sign(r.return_1y)}</span>`],
        ["Volatility", pct(r.volatility_20d, 0)],
        ["RSI (14)", num(r.rsi_14, 0)],
        ["Trend", `<span class="tag ${r.trend}">${r.trend}</span>`],
      ].map(([l, v]) => `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("");
      const anomPts = s.c.map((v, i) => (s.a.includes(i) ? v : null));
      const accent = color(r.sector_name);
      price?.destroy(); rsi?.destroy();
      price = new Chart(document.getElementById("priceChart"), {
        type: "line",
        data: {
          labels: s.d,
          datasets: [
            { label: "Close", data: s.c, borderColor: accent, backgroundColor: accent },
            { label: "50-day avg", data: s.s50, borderColor: css("--ink-2"), backgroundColor: css("--ink-2"), borderWidth: 1.5 },
            { label: "200-day avg", data: s.s200, borderColor: css("--muted"), backgroundColor: css("--muted"), borderWidth: 1.5, borderDash: [5, 4] },
            { label: "Anomaly", data: anomPts, showLine: false, pointRadius: 5, pointHoverRadius: 6, borderColor: css("--surface"), borderWidth: 2, backgroundColor: css("--down") },
          ],
        },
        options: {
          maintainAspectRatio: false, interaction: lineInteraction,
          plugins: { tooltip: { filter: (i) => i.raw != null, callbacks: { title: (i) => fmtDate(i[0].label), label: (c) => ` ${c.dataset.label}: ₹${num(c.raw)}` } } },
          scales: { x: { ticks: { maxTicksLimit: 8, callback: function (v) { return fmtDate(this.getLabelForValue(v)).replace(/^\d+ /, ""); } }, grid: { display: false } },
                    y: { grid: { color: css("--grid") }, ticks: { callback: (v) => "₹" + v.toLocaleString("en-IN") } } },
        },
      });
      rsi = new Chart(document.getElementById("rsiChart"), {
        type: "line",
        data: { labels: s.d, datasets: [{ label: "RSI (14)", data: s.rsi, borderColor: accent, backgroundColor: accent, borderWidth: 1.5 }] },
        options: {
          maintainAspectRatio: false, interaction: lineInteraction,
          plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => fmtDate(i[0].label), label: (c) => ` RSI: ${c.raw}` } } },
          scales: {
            x: { ticks: { maxTicksLimit: 8, callback: function (v) { return fmtDate(this.getLabelForValue(v)).replace(/^\d+ /, ""); } }, grid: { display: false } },
            y: { min: 0, max: 100,
                 afterBuildTicks: (ax) => { ax.ticks = [0, 30, 70, 100].map((value) => ({ value })); },
                 ticks: { callback: (v) => ([30, 70].includes(v) ? v + (v === 70 ? " overbought" : " oversold") : v) },
                 grid: { color: (c) => ([30, 70].includes(c.tick.value) ? css("--axis") : "transparent") } },
          },
        },
      });
    };
    sel.addEventListener("change", () => draw(true));
    draw();
    if (fromHash) document.getElementById("stocks").scrollIntoView();
  }

  function renderMap(rows) {
    const cities = {};
    rows.forEach((r) => {
      const k = r.city_name;
      (cities[k] ||= { city: r.city_name, state: r.state, lat: r.latitude, lon: r.longitude, cos: [] }).cos.push(r);
    });
    const el = document.getElementById("map");
    const map = L.map(el, {
      scrollWheelZoom: true,          // gated below so the page still scrolls normally
      zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 90, wheelDebounceTime: 25,
    }).setView([22.5, 79], 5);
    // Trackpad pinch arrives as a wheel event with ctrlKey = true, so it always zooms (Ctrl + scroll on a mouse too).
    // A plain two-finger scroll zooms only after the map is clicked; otherwise the page scrolls and a hint appears.
    let active = false, hintTimer;
    const hint = L.DomUtil.create("div", "map-hint", el);
    hint.textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "Pinch to zoom, or click the map to zoom by scrolling" : "Ctrl + scroll to zoom, or click the map to zoom by scrolling";
    el.addEventListener("wheel", (e) => {
      if (active || e.ctrlKey) return;            // let Leaflet zoom
      e.stopPropagation();                        // keep Leaflet from zooming; the page scrolls as normal
      hint.classList.add("show"); clearTimeout(hintTimer);
      hintTimer = setTimeout(() => hint.classList.remove("show"), 1400);
    }, { capture: true });
    map.on("click", () => { active = true; hint.classList.remove("show"); });
    el.addEventListener("mouseleave", () => { active = false; });
    // OpenStreetMap's free standard tiles (no API key). In dark mode they're darkened with a CSS filter.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 10,
      className: dark ? "tiles-dark" : "",
    }).addTo(map);
    map.fitBounds(L.latLngBounds(Object.values(cities).map((c) => [c.lat, c.lon])).pad(0.25));
    Object.values(cities).forEach((c) => {
      const avg = c.cos.reduce((a, r) => a + (r.momentum_20d || 0), 0) / c.cos.length;
      const fill = diverge(avg, 0.08).bg;
      L.circleMarker([c.lat, c.lon], {
        radius: 7 + 4 * Math.sqrt(c.cos.length), color: css("--surface"), weight: 2, fillColor: fill, fillOpacity: 0.9,
      }).addTo(map).bindPopup(
        `<strong>${esc(c.city)}, ${esc(c.state)}</strong><br>${c.cos.length} compan${c.cos.length > 1 ? "ies" : "y"} · avg 1-month ${sign(avg)}<br>` +
        c.cos.map((r) => `${esc(r.company_name)} (${esc(r.sector_name)}): <span class="${cls(r.momentum_20d)}">${sign(r.momentum_20d)}</span>`).join("<br>") +
        `<br><a href="https://www.google.com/maps/dir/?api=1&destination=${c.lat},${c.lon}" target="_blank" rel="noopener">Directions to ${esc(c.city)} ↗</a>`
      ).bindTooltip(`${c.city}: ${c.cos.length}`, { direction: "top" });
    });
  }

  function renderAnomalies(rows) {
    const reason = { price_spike: "Price spike", volume_spike: "Volume spike", price_and_volume_spike: "Price + volume" };
    document.getElementById("anomTable").innerHTML = rows.length
      ? `<tr><th>Date</th><th>Company</th><th>Sector</th><th class="num">Move</th><th class="num">Volume z</th><th>Type</th></tr>` +
        rows.slice(0, 25).map((r) => `<tr><td>${fmtDate(r.price_date)}</td><td>${esc(r.company_name)}</td>
          <td><span class="swatch" style="background:${color(r.sector_name)}"></span>${esc(r.sector_name)}</td>
          <td class="num ${cls(r.return_1d)}">${sign(r.return_1d, 2)}</td><td class="num">${num(r.volume_zscore_20d, 1)}</td>
          <td>${reason[r.anomaly_reason] || esc(r.anomaly_reason)}</td></tr>`).join("")
      : "<tr><td>No anomalies in the last 90 days.</td></tr>";
  }

  function renderBacktest(b) {
    if (!b || !b.curve.length) return;
    new Chart(document.getElementById("btChart"), {
      type: "line",
      data: {
        labels: b.curve.map((r) => r.month),
        datasets: [
          { label: "Momentum rotation", data: b.curve.map((r) => r.strategy), borderColor: css("--s-it"), backgroundColor: css("--s-it") },
          { label: "All 5 sectors equally", data: b.curve.map((r) => r.equal), borderColor: css("--s-banking"), backgroundColor: css("--s-banking") },
        ],
      },
      options: {
        maintainAspectRatio: false, interaction: lineInteraction,
        plugins: { tooltip: { callbacks: {
          label: (c) => ` ${c.dataset.label}: ₹${c.raw.toFixed(0)}`,
          afterBody: (i) => "Held: " + b.curve[i[0].dataIndex].pick,
        } } },
        scales: { x: yearAxis(b.curve.map((r) => r.month)),
                  y: { grid: { color: css("--grid") }, ticks: { callback: (v) => "₹" + v } } },
      },
    });
    const S = b.strategy, E = b.equal;
    const rows = [
      ["₹100 grew to", "₹" + (100 * (1 + S.total_return)).toFixed(0), "₹" + (100 * (1 + E.total_return)).toFixed(0)],
      ["Yearly return (CAGR)", pct(S.cagr), pct(E.cagr)],
      ["Volatility (yearly)", pct(S.volatility), pct(E.volatility)],
      ["Sharpe ratio", num(S.sharpe), num(E.sharpe)],
      ["Worst drawdown", pct(S.max_drawdown), pct(E.max_drawdown)],
      ["Best / worst month", `${sign(S.best_month)} / ${sign(S.worst_month)}`, `${sign(E.best_month)} / ${sign(E.worst_month)}`],
    ];
    document.getElementById("btTable").innerHTML =
      `<tr><th></th><th class="num">Rotation</th><th class="num">Equal</th></tr>` +
      rows.map(([l, a, e]) => `<tr><td>${l}</td><td class="num">${a}</td><td class="num">${e}</td></tr>`).join("") +
      `<tr><td colspan="3" class="note" style="white-space:normal">${b.months} months (${b.start} to ${b.end}). Rotation beat equal-weight in ${pct(b.win_rate, 0)} of months.
       Most-held: ${Object.entries(b.picks).map(([k, v]) => `${k} ${v}`).join(", ")}.</td></tr>`;
  }

  function renderFacts(s, sec) {
    const years = new Date(s.latest_date).getFullYear() - new Date(s.first_date).getFullYear() + 1;
    const facts = [
      [(s.price_rows + s.signal_rows).toLocaleString("en-IN"), "rows of prices and signals"],
      [years + " years", "of daily history per stock"],
      ["8 checks", "of data quality on every load"],
      ["0", "manual steps per day"],
    ];
    document.getElementById("facts").innerHTML =
      facts.map(([b, t]) => `<div class="fact"><b>${b}</b><span>${t}</span></div>`).join("");
  }

  function renderPipeline(rows) {
    const icon = { success: "✅", partial: "⚠️", failed: "❌", running: "⏳" };
    const name = { daily_update: "Daily price update", data_quality: "Data-quality checks", backfill: "History backfill", fundamentals: "Fundamentals snapshot" };
    document.getElementById("pipeTable").innerHTML =
      `<tr><th>Job</th><th>Last finished</th><th>Status</th><th>Details</th></tr>` +
      rows.map((r) => `<tr><td>${name[r.job_name] || esc(r.job_name)}</td>
        <td>${r.finished_at ? new Date(r.finished_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "–"}</td>
        <td>${icon[r.status] || ""} ${esc(r.status)}</td><td style="white-space:normal">${esc(r.message)}</td></tr>`).join("");
  }

  // ---------- page UI: theme, copy, toast, progress, back-to-top ----------
  function toast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 2200);
  }
  function copyText(text, msg) {
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
      .then(() => toast(msg || "Copied"))
      .catch(() => toast("Couldn't copy, please select and copy manually"));
  }
  document.querySelectorAll("[data-copy]").forEach((b) =>
    b.addEventListener("click", () => copyText(document.getElementById(b.dataset.copy).innerText, "SQL copied")));
  const themeBtn = document.getElementById("themeToggle");
  themeBtn.textContent = dark ? "☀ Light" : "☾ Dark";
  themeBtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  themeBtn.addEventListener("click", () => {
    const next = dark ? "light" : "dark";
    try { localStorage.setItem("theme", next); } catch (e) {}
    document.documentElement.dataset.theme = next;
    location.reload();                       // charts and map re-render in the new colours
  });
  const bar = document.getElementById("progress"), top = document.getElementById("toTop");
  const onScroll = () => {
    const h = document.documentElement, max = h.scrollHeight - h.clientHeight;
    bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + "%";
    top.classList.toggle("show", h.scrollTop > 600);
  };
  addEventListener("scroll", onScroll, { passive: true });
  top.addEventListener("click", () => scrollTo({ top: 0 }));
  onScroll();
})();
