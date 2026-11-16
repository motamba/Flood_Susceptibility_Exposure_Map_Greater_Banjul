/* Web map logic (ToR §5 step 10; Implementation Plan §9).
 * Layers are loaded from docs/data/, which scripts/09_export_web.py writes.
 * Layers whose data file is missing are listed as "not available".
 */

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";

// Susceptibility palette: colour-blind-safe sequential yellow → dark red (Plan §9).
const SUSC_COLOURS = { Low: "#fed976", Medium: "#fd8d3c", High: "#bd0026" };

const GROUP_LABELS = {
  banjul: "Banjul City Council",
  kanifing: "Kanifing Municipal Council",
  kombo_north: "Kombo North (West Coast Region)",
};

// Drawing order: first entry is drawn at the bottom.
const LAYERS = [
  {
    id: "susceptibility", label: "Flood susceptibility", visible: true,
    layers: [{
      type: "fill",
      paint: {
        "fill-color": ["match", ["get", "class"],
          "Low", SUSC_COLOURS.Low, "Medium", SUSC_COLOURS.Medium,
          "High", SUSC_COLOURS.High, "#cccccc"],
        "fill-opacity": 0.65,
      },
    }],
    legend: { title: "Susceptibility", items: Object.entries(SUSC_COLOURS).map(([k, c]) => ({ label: k, colour: c })) },
    popup: (p) => `<div class="popup"><h4>${p.class} susceptibility</h4><table>
      <tr><td>Class area</td><td>${Number(p.area_km2).toFixed(1)} km² in the study area</td></tr></table>
      <p class="small">Relative index from terrain and land cover, not a flood forecast.</p></div>`,
  },
  {
    id: "water", label: "Water and wetland", visible: true,
    layers: [{
      type: "fill",
      paint: {
        "fill-color": ["match", ["get", "type"], "water", "#4a90c8", "wetland", "#5fb58a", "#999999"],
        "fill-opacity": ["match", ["get", "type"], "water", 0.7, 0.45],
      },
    }],
    legend: { title: "Water and wetland", items: [
      { label: "Open water (excluded from index)", colour: "#4a90c8" },
      { label: "Wetland / mangrove (Tanbi)", colour: "#5fb58a" }] },
    popup: (p) => `<div class="popup"><h4>${p.type === "water" ? "Open water" : "Wetland / mangrove"}</h4>
      <table><tr><td>Total area</td><td>${Number(p.area_km2).toFixed(1)} km²</td></tr></table></div>`,
  },
  {
    id: "units", label: "Districts", visible: true,
    layers: [
      { type: "fill", paint: { "fill-color": "#000000", "fill-opacity": 0 } }, // invisible, for popups
      { type: "line", paint: { "line-color": "#555555", "line-width": 0.8, "line-dasharray": [2, 2] } },
    ],
    legend: { title: "Boundaries", items: [{ label: "District (ADM2)", colour: "#555555", kind: "line dashed" }] },
    popup: (p) => `<div class="popup"><h4>${p.shapeName}</h4><table>
      <tr><td>Council</td><td>${GROUP_LABELS[p.group] || p.group}</td></tr>
      <tr><td>Area</td><td>${Number(p.area_km2).toFixed(1)} km²</td></tr></table></div>`,
  },
  {
    id: "study_area", label: "Study area outline", visible: true,
    layers: [{ type: "line", paint: { "line-color": "#111111", "line-width": 2.2 } }],
    legend: { title: "", items: [{ label: "Study area (incl. Tanbi Wetland)", colour: "#111111", kind: "line" }] },
  },
  {
    id: "facilities", label: "Schools and health facilities", visible: true,
    layers: [{
      type: "circle",
      paint: {
        "circle-radius": 5,
        "circle-color": ["match", ["get", "type"], "school", "#2c7fb8", "health", "#d7301f", "#666666"],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 1,
      },
    }],
    legend: { title: "Facilities", items: [
      { label: "School", colour: "#2c7fb8", kind: "dot" },
      { label: "Health facility", colour: "#d7301f", kind: "dot" }] },
    popup: (p) => `<div class="popup"><h4>${esc(p.name) || "Unnamed " + (p.type === "school" ? "school" : "health facility")}</h4><table>
      <tr><td>Type</td><td>${esc(p.kind)}</td></tr>
      <tr><td>District</td><td>${esc(p.district) || "–"}</td></tr>
      <tr><td>Susceptibility</td><td><b>${p.class}</b></td></tr></table>
      <p class="small">Source: © OpenStreetMap contributors</p></div>`,
  },
  {
    id: "reported_floods", label: "Reported flood locations", visible: false,
    layers: [{ type: "circle", paint: { "circle-radius": 6, "circle-color": "#6a3d9a", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } }],
    legend: { title: "Plausibility", items: [{ label: "Reported flood location (news)", colour: "#6a3d9a", kind: "dot" }] },
    popup: (p) => `<div class="popup"><h4>${p.id} ${esc(p.place)}</h4><table>
      <tr><td>Reported</td><td>${esc(p.event_date)}</td></tr>
      <tr><td>Map class</td><td><b>${p.class}</b></td></tr></table>
      <p class="small"><a href="${encodeURI(p.source_url)}" target="_blank" rel="noopener">${esc(p.source_title)}</a></p></div>`,
  },
];

// Escape text from data files before inserting it as HTML (OSM names, news titles).
function esc(v) {
  if (v === null || v === undefined || v === "null") return "";
  return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const fmt = (v, unit) => (unit === "count" ? Math.round(v).toLocaleString("en-GB")
  : Number(v).toLocaleString("en-GB", { maximumFractionDigits: unit === "km" ? 0 : 1 }));

async function buildExposure(file) {
  const box = document.getElementById("exposure-body");
  let t;
  try {
    t = await (await fetch(`data/${file}`, { cache: "no-store" })).json();
  } catch {
    return;
  }
  const units = { km2: "km²", km: "km", count: "" };
  const rows = t.exposure.map((r) => {
    const ref = r.asset === "Land area";
    return `<tr class="${ref ? "ref" : ""}"><td>${r.asset}${units[r.unit] ? ` (${units[r.unit]})` : ""}</td>
      <td class="high">${fmt(r.High, r.unit)}</td><td class="high">${r.High_pct.toFixed(0)}%</td>
      <td>${fmt(r.Total, r.unit)}</td></tr>`;
  }).join("");
  const councils = { banjul: "Banjul", kanifing: "Kanifing", kombo_north: "Kombo North" };
  const byCouncil = (t.by_council || []).map((r) => `<tr><td>${councils[r.council] || r.council}</td>
      <td class="high">${r.buildings_high.toLocaleString("en-GB")}</td><td class="high">${r.buildings_high_pct.toFixed(0)}%</td>
      <td>${r.schools_high}</td><td>${r.health_high}</td></tr>`).join("");
  const p = t.plausibility;
  box.innerHTML = `
    <p>Assets inside the <b>High</b> susceptibility class. The land-area row is the reference: an asset type is over-exposed when its share is above it.</p>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>Asset</th><th>In High</th><th>Share</th><th>Total</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    ${byCouncil ? `<h2>High class by council</h2><div class="table-wrap"><table class="data-table">
      <thead><tr><th>Council</th><th>Buildings</th><th>Share</th><th>Schools</th><th>Health</th></tr></thead>
      <tbody>${byCouncil}</tbody></table></div>` : ""}
    ${p ? `<h2>Plausibility check</h2>
      <p>${Math.round(p["share of points in High"] * p.points)} of ${p.points} places reported as flooded in the news (2022–2026) lie in the High class, which covers ${Math.round(p["share of land in High"] * 100)}% of the land. All ${p.points} lie in High or Medium. Too few points to prove skill, but consistent with the map. Turn on <i>Reported flood locations</i> in the Layers tab.</p>` : ""}
    <p class="small">OSM data as of ${t.osm_data_as_of.slice(0, 10)}. Roads exclude footpaths and tracks.</p>`;
}

// Rainfall tab (ToR §5 step 9; Plan §7 outputs 1 and 2, §9 Chart.js).
// Same colours as outputs/figures/rainfall.png.
const RAIN = { bar: "#4a90c8", example: "#1f2328", threshold: "#bd0026", grid: "#e5e5e5", text: "#5b636e" };
let rainData = null;
let rainDrawn = false;

const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const shortDate = (iso) => { const [, m, d] = iso.split("-"); return `${Number(d)} ${MONTH_ABBR[Number(m) - 1]}`; };

async function buildRainfall(file) {
  try {
    rainData = await (await fetch(`data/${file}`, { cache: "no-store" })).json();
  } catch {
    return;
  }
  const r = rainData, c = r.climatology, d = r.daily;
  const julyIdx = 6;
  const julyRatio = c.example_mm[julyIdx] / c.normal_mm[julyIdx];
  const peaks = d ? d.peaks.map((p) => `${shortDate(p.date)} (${Math.round(p.mm)} mm)`).join(" and ") : "";
  const rows = c.months.map((m, i) => `<tr><td>${m}</td><td>${c.normal_mm[i].toFixed(0)}</td><td>${c.example_mm[i].toFixed(0)}</td></tr>`).join("");
  document.getElementById("rainfall-body").innerHTML = `
    <p>Almost all rain falls in one short season: <b>${Math.round(c.wet_season_share * 100)}%</b> of the ${c.normal_annual_mm.toLocaleString("en-GB")} mm annual normal falls June–October.</p>
    <h3>Monthly rainfall (mm)</h3>
    <div class="chart-box"><canvas id="chart-monthly" role="img"
      aria-label="Bar chart of mean monthly rainfall ${c.period}, with ${c.example_year} as dots. Table below."></canvas></div>
    <p class="small">Bars: normal ${c.period}. Dots: ${c.example_year} (${c.example_annual_mm.toLocaleString("en-GB")} mm in the year; July was ${julyRatio.toFixed(1)}× its normal).</p>
    ${d ? `<h3>Daily rainfall, June–October ${d.year} (mm)</h3>
    <div class="chart-box"><canvas id="chart-daily" role="img"
      aria-label="Bar chart of daily rainfall June to October ${d.year} with a heavy-rain line at ${r.threshold_mm} mm."></canvas></div>
    <p class="small">Dashed line: ${r.threshold_mm} mm/day heavy rain (illustrative). Exceeded on ${d.days_over_threshold} days: ${peaks}.
      The flood of 30–31 July 2022 shows clearly. CHIRPS daily values are 5-day totals spread over the days, so single-day peaks are understated
      (Banjul airport measured 276 mm over 30–31 July).</p>` : ""}
    <div class="callout"><b>Early-warning link.</b> When more than about ${r.threshold_mm} mm of rain falls in a day, the <b>High</b> zones on the map
      are where water is most likely to pond first. The threshold is illustrative, not an official warning level.</div>
    <details><summary>Monthly values as a table</summary>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>Month</th><th>Normal</th><th>${c.example_year}</th></tr></thead>
      <tbody>${rows}</tbody></table></div></details>
    <p class="small">${r.source}; ${r.n_cells} cells (about 5.5 km), weighted by land area in the study area.</p>`;
  if (!document.getElementById("tab-rainfall").hidden) drawRainCharts();
}

// Chart.js needs a visible canvas to size itself, so draw on first opening of the tab.
function drawRainCharts() {
  if (rainDrawn || !rainData || typeof Chart === "undefined") return;
  rainDrawn = true;
  const r = rainData, c = r.climatology, d = r.daily;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = RAIN.text;
  const axes = (ytitle) => ({
    x: { grid: { display: false } },
    y: { beginAtZero: true, grid: { color: RAIN.grid }, title: { display: true, text: ytitle } },
  });

  new Chart(document.getElementById("chart-monthly"), {
    data: {
      labels: c.months,
      datasets: [
        { type: "bar", label: `Normal ${c.period}`, data: c.normal_mm, backgroundColor: RAIN.bar,
          borderRadius: 3, barPercentage: 0.75, order: 2 },
        { type: "line", label: String(c.example_year), data: c.example_mm, showLine: false,
          pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: RAIN.example,
          pointBorderColor: "#ffffff", pointBorderWidth: 1.5, order: 1 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", align: "start", labels: { boxWidth: 10, boxHeight: 10 } },
        tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(0)} mm` } },
      },
      scales: axes("mm per month"),
    },
  });

  if (!d) return;
  const peakSet = new Set(d.peaks.map((p) => p.date));
  // Small plugin: write the date and amount above the days over the threshold.
  const peakLabels = {
    id: "peakLabels",
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      const meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.fillStyle = RAIN.example;
      ctx.font = `600 10px ${Chart.defaults.font.family}`;
      ctx.textAlign = "center";
      d.dates.forEach((iso, i) => {
        if (!peakSet.has(iso)) return;
        const bar = meta.data[i];
        ctx.fillText(`${shortDate(iso)}: ${Math.round(d.mm[i])} mm`, bar.x, bar.y - 5);
      });
      ctx.restore();
    },
  };
  new Chart(document.getElementById("chart-daily"), {
    data: {
      labels: d.dates.map(shortDate),
      datasets: [
        { type: "bar", label: "Daily rainfall", data: d.mm, backgroundColor: RAIN.bar,
          borderRadius: 2, barPercentage: 1, categoryPercentage: 0.9, order: 2 },
        { type: "line", label: `${r.threshold_mm} mm/day`, data: d.dates.map(() => r.threshold_mm),
          borderColor: RAIN.threshold, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0,
          pointHitRadius: 0, order: 1 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => item.datasetIndex === 0,
          callbacks: { label: (ctx) => ` ${ctx.parsed.y.toFixed(1)} mm` },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { autoSkip: false, maxRotation: 0,
          callback: (v, i) => (d.dates[i].endsWith("-01") ? MONTH_ABBR[Number(d.dates[i].slice(5, 7)) - 1] : "") } },
        y: { beginAtZero: true, suggestedMax: Math.max(...d.mm) * 1.2, grid: { color: RAIN.grid },
          title: { display: true, text: "mm per day" } },
      },
    },
    plugins: [peakLabels],
  });
}

// Fallback basemap if the OpenFreeMap style cannot be loaded.
const FALLBACK_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6 } }],
};

let usedFallback = false;
const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  center: [-16.68, 13.41],
  zoom: 11,
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl(), "top-left");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
map.addControl(new maplibregl.AttributionControl({
  compact: true,
  customAttribution: [
    "Terrain: FABDEM (Univ. of Bristol, CC BY-NC-SA 4.0), from Copernicus DEM GLO-30 © DLR, Airbus, provided under COPERNICUS by the EU and ESA",
    "Land cover: ESA WorldCover 2021 (CC BY 4.0)",
    "Rainfall: CHIRPS v3 (Climate Hazards Center, UCSB)",
    "Assets: © OpenStreetMap contributors (ODbL)",
    "Boundaries: geoBoundaries (CC BY 4.0)",
  ].join(" · "),
}));

async function loadMeta() {
  try {
    const r = await fetch("data/meta.json", { cache: "no-store" });
    return r.ok ? await r.json() : { layers: {} };
  } catch {
    return { layers: {} };
  }
}

function sublayerIds(def) {
  return def.layers.map((_, i) => `${def.id}-${i}`);
}

function setVisible(def, on) {
  for (const id of sublayerIds(def)) {
    map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }
}

function addLayer(def, file) {
  map.addSource(def.id, { type: "geojson", data: `data/${file}` });
  def.layers.forEach((l, i) => {
    map.addLayer({
      id: `${def.id}-${i}`, source: def.id, type: l.type, paint: l.paint,
      layout: { visibility: def.visible ? "visible" : "none" },
    });
  });
  if (def.popup) {
    const clickId = `${def.id}-0`;
    map.on("click", clickId, (e) => {
      new maplibregl.Popup({ maxWidth: "260px" })
        .setLngLat(e.lngLat).setHTML(def.popup(e.features[0].properties)).addTo(map);
    });
    map.on("mouseenter", clickId, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", clickId, () => { map.getCanvas().style.cursor = ""; });
  }
}

function buildLayerList(available) {
  const ul = document.getElementById("layer-list");
  // Top of the list = top of the map, so list in reverse drawing order.
  for (const def of [...LAYERS].reverse()) {
    const ready = Boolean(available[def.id]);
    const li = document.createElement("li");
    li.className = ready ? "" : "pending";
    const inputId = `toggle-${def.id}`;
    li.innerHTML = `<input type="checkbox" id="${inputId}" ${ready && def.visible ? "checked" : ""} ${ready ? "" : "disabled"}>
      <label for="${inputId}">${def.label}</label>
      ${ready ? "" : `<span class="badge">not available</span>`}`;
    if (ready) {
      li.querySelector("input").addEventListener("change", (e) => setVisible(def, e.target.checked));
    }
    ul.appendChild(li);
  }
}

function buildLegend(available) {
  const box = document.getElementById("legend");
  for (const def of LAYERS) {
    if (!available[def.id] || !def.legend) continue;
    const g = document.createElement("div");
    g.className = "legend-group";
    g.innerHTML = (def.legend.title ? `<div class="lg-title">${def.legend.title}</div>` : "") +
      def.legend.items.map((it) => {
        const kind = it.kind || "";
        const style = kind.startsWith("line") ? `border-top-color:${it.colour}` : `background:${it.colour}`;
        return `<div class="legend-item"><span class="swatch ${kind}" style="${style}"></span>${it.label}</div>`;
      }).join("");
    box.appendChild(g);
  }
  if (!box.children.length) box.textContent = "No layers built yet.";
}

function fitToStudyArea() {
  const src = map.getSource("study_area");
  if (!src) return;
  fetch("data/study_area.geojson").then((r) => r.json()).then((gj) => {
    const b = new maplibregl.LngLatBounds();
    const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
    gj.features.forEach((f) => walk(f.geometry.coordinates));
    map.fitBounds(b, { padding: 30, duration: 0 });
  });
}

map.on("error", (e) => {
  // A failed style request fires before "load"; switch basemap once.
  if (!usedFallback && !map.isStyleLoaded() && e.error && /style|fetch|Failed/i.test(String(e.error.message))) {
    usedFallback = true;
    console.warn("OpenFreeMap style failed, using OSM raster fallback:", e.error.message);
    map.setStyle(FALLBACK_STYLE);
  }
});

// Panel tables and charts do not depend on the map, so load them straight away.
const metaReady = loadMeta();
metaReady.then((meta) => {
  const available = meta.layers || {};
  if (available.exposure_tables) buildExposure(available.exposure_tables);
  if (available.rainfall) buildRainfall(available.rainfall);
  if (meta.built_utc) document.getElementById("built").textContent = meta.built_utc;
});

map.on("load", async () => {
  const meta = await metaReady;
  const available = meta.layers || {};
  for (const def of LAYERS) {
    if (available[def.id]) addLayer(def, available[def.id]);
  }
  buildLayerList(available);
  buildLegend(available);
  fitToStudyArea();
});

// Tabs
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", String(on));
      document.getElementById(`tab-${b.dataset.tab}`).hidden = !on;
    });
    if (btn.dataset.tab === "rainfall") drawRainCharts();
  });
});

// Open a tab from the URL, e.g. index.html#rainfall
const startTab = [...document.querySelectorAll(".tab")].find((b) => `#${b.dataset.tab}` === location.hash);
if (startTab) startTab.click();

// Mobile drawer toggle
const panel = document.getElementById("panel");
document.getElementById("panel-toggle").addEventListener("click", (e) => {
  const collapsed = panel.classList.toggle("collapsed");
  e.currentTarget.setAttribute("aria-expanded", String(!collapsed));
  e.currentTarget.textContent = collapsed ? "Show panel" : "Hide panel";
  map.resize();
});
