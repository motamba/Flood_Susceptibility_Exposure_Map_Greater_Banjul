/* Web map logic (ToR §5 step 10; Implementation Plan §9).
 * Layers are loaded from docs/data/, which scripts/09_export_web.py writes.
 * Layers whose data is not built yet are listed as "pending" with the day
 * of the work plan (Plan §11) when they arrive.
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
    id: "susceptibility", label: "Flood susceptibility", pending: "Day 5", visible: true,
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
    popup: (p) => `<h4>${p.class} susceptibility</h4>`,
  },
  {
    id: "water", label: "Water and wetland", pending: "Day 4", visible: true,
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
    id: "facilities", label: "Schools and health facilities", pending: "Day 6", visible: true,
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
    popup: (p) => `<div class="popup"><h4>${p.name || "Unnamed"}</h4><table>
      <tr><td>Type</td><td>${p.type}</td></tr>
      <tr><td>Susceptibility</td><td>${p.class}</td></tr></table></div>`,
  },
  {
    id: "reported_floods", label: "Reported flood locations", pending: "Day 6", visible: false,
    layers: [{ type: "circle", paint: { "circle-radius": 6, "circle-color": "#6a3d9a", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } }],
    legend: { title: "Plausibility", items: [{ label: "Reported flood location", colour: "#6a3d9a", kind: "dot" }] },
  },
];

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
  customAttribution: "Boundaries: geoBoundaries (CC BY 4.0)",
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
      ${ready ? "" : `<span class="badge">pending · ${def.pending}</span>`}`;
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

map.on("load", async () => {
  const meta = await loadMeta();
  const available = meta.layers || {};
  for (const def of LAYERS) {
    if (available[def.id]) addLayer(def, available[def.id]);
  }
  buildLayerList(available);
  buildLegend(available);
  fitToStudyArea();
  if (meta.built_utc) document.getElementById("built").textContent = meta.built_utc;
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
  });
});

// Mobile drawer toggle
const panel = document.getElementById("panel");
document.getElementById("panel-toggle").addEventListener("click", (e) => {
  const collapsed = panel.classList.toggle("collapsed");
  e.currentTarget.setAttribute("aria-expanded", String(!collapsed));
  map.resize();
});
