/* ═══════════════════════════════════════════════════════════
   MTL Mobility Predictor — Dashboard JS
   Connects to all FastAPI endpoints and drives the UI.
   ═══════════════════════════════════════════════════════════ */

'use strict';

/* ── CONFIG ─────────────────────────────────────────────── */
const BASE        = '';          // same origin
const REFRESH_MS  = 30_000;      // 30 s auto-refresh
const MAX_VEHICLES = 400;        // normalise KPI bar

/* ── LEAFLET MAP ─────────────────────────────────────────── */
const map = L.map('map', {
  center:           [45.5017, -73.5673],
  zoom:             12,
  zoomControl:      true,
  attributionControl: false,
  preferCanvas:     true,
});

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
}).addTo(map);

// Keep attribution tiny + respect OSM requirement
L.control.attribution({ prefix: '© OpenStreetMap' }).addTo(map);

const _markers = new Map();   // vehicle_id → L.Marker

function _delayColor(seconds) {
  if (Math.abs(seconds) < 60)  return '#34D399';   // on time
  if (Math.abs(seconds) < 180) return '#FBBF24';   // minor delay
  return '#F87171';                                  // significant
}

function _busIcon(routeId, delaySec) {
  const c = _delayColor(delaySec);
  return L.divIcon({
    className: '',
    iconSize:  [24, 24],
    iconAnchor:[12, 12],
    html: `<div style="
      width:24px;height:24px;border-radius:50%;
      background:${c};
      display:flex;align-items:center;justify-content:center;
      font-size:8px;font-weight:800;color:#000;letter-spacing:-0.03em;
      border:2px solid rgba(0,0,0,0.25);
      box-shadow:0 0 10px ${c}99,0 2px 6px rgba(0,0,0,0.4);
    ">${String(routeId).slice(0,3)}</div>`,
  });
}

/* ── UTILITY ─────────────────────────────────────────────── */
const $  = id  => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

function setText(id, val) {
  const el = $(id);
  if (el) el.textContent = val;
}

function setClass(id, cls) {
  const el = $(id);
  if (el) el.className = cls;
}

function setDot(id, state) {
  // state: 'ok' | 'warn' | 'error' | '' (unknown)
  const el = $(id);
  if (el) el.className = `h-dot${state ? ' ' + state : ''}`;
}

function setKpi(valueId, fillId, subId, { value, unit = '', fillPct = 0, sub = '', fillClass = '' }) {
  const valEl = $(valueId);
  if (valEl) {
    valEl.innerHTML = value + (unit
      ? `<span class="kpi-unit">${unit}</span>`
      : '');
  }
  const fillEl = $(fillId);
  if (fillEl) {
    fillEl.style.width    = Math.min(fillPct, 100) + '%';
    fillEl.className      = 'kpi-fill' + (fillClass ? ' ' + fillClass : '');
  }
  if (subId) setText(subId, sub);
}

/* Animated numeric counter */
function animateTo(el, target, decimals = 0, suffix = '') {
  if (!el) return;
  const start   = parseFloat(el.dataset.current || '0');
  const diff    = target - start;
  const dur     = 700;
  const t0      = performance.now();

  function tick(now) {
    const progress = Math.min((now - t0) / dur, 1);
    const ease     = 1 - Math.pow(1 - progress, 3);   // ease-out cubic
    const val      = start + diff * ease;
    el.dataset.current = val;
    el.textContent = val.toFixed(decimals) + suffix;
    if (progress < 1) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

/* ── WEATHER CONSTANTS ───────────────────────────────────── */
const WEATHER_ICON = {
  0:'☀️',1:'🌤',2:'⛅',3:'☁️',
  45:'🌫',48:'🌫',
  51:'🌦',53:'🌦',55:'🌧',
  61:'🌧',63:'🌧',65:'🌧',
  71:'🌨',73:'🌨',75:'❄️',77:'❄️',
  80:'🌦',81:'🌧',82:'⛈',
  95:'⛈',96:'⛈',99:'⛈',
};

const WEATHER_DESC = {
  0:'Ciel dégagé',1:'Principalement dégagé',2:'Partiellement nuageux',3:'Couvert',
  45:'Brouillard',48:'Brouillard givrant',
  51:'Bruine légère',53:'Bruine modérée',55:'Bruine dense',
  61:'Pluie légère',63:'Pluie modérée',65:'Pluie forte',
  71:'Neige légère',73:'Neige modérée',75:'Neige forte',77:'Grésil',
  80:'Averses légères',81:'Averses modérées',82:'Averses fortes',
  95:'Orage',96:'Orage avec grêle',99:'Orage violent',
};

/* ── FETCH HELPERS ───────────────────────────────────────── */
async function apiFetch(path) {
  const res = await fetch(BASE + path);
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${path}`);
  return res.json();
}

/* ─────────────────────────────────────────────────────────
   DATA LOADERS
   ───────────────────────────────────────────────────────── */

/* 1. Health ─────────────────────────────────────────────── */
async function loadHealth() {
  const t0 = performance.now();
  try {
    const h       = await apiFetch('/health');
    const latency = Math.round(performance.now() - t0);

    // Topbar status chip
    const chip = $('api-status-chip');
    chip.className  = 'status-chip';
    $('api-status-text').textContent = `En ligne · ${latency} ms`;

    // Health rows
    setDot('dot-api', 'ok');
    setText('val-api', `${latency} ms`);

    setDot('dot-db', h.db_ok ? 'ok' : 'error');
    setText('val-db', h.db_ok ? 'Connectée' : 'Erreur');

    setDot('dot-model', h.model_loaded ? 'ok' : 'warn');
    setText('val-model', h.model_loaded ? 'Chargé' : 'Non chargé');

  } catch {
    const chip = $('api-status-chip');
    chip.className = 'status-chip offline';
    $('api-status-text').textContent = 'Hors ligne';
    setDot('dot-api', 'error');
    setText('val-api', 'Erreur');
  }
}

/* 2. Vehicles (map + KPI) ───────────────────────────────── */
async function loadVehicles() {
  try {
    const vehicles = await apiFetch('/vehicles/live?limit=500');

    // Map
    const seen = new Set(vehicles.map(v => v.vehicle_id));

    // Remove stale
    _markers.forEach((marker, id) => {
      if (!seen.has(id)) { map.removeLayer(marker); _markers.delete(id); }
    });

    vehicles.forEach(v => {
      const icon    = _busIcon(v.route_id, v.avg_delay_seconds);
      const popup   = `<b>Ligne ${v.route_id}</b><br>Véhicule : ${v.vehicle_id}<br>Délai moyen : ${v.avg_delay_seconds} s`;
      if (_markers.has(v.vehicle_id)) {
        _markers.get(v.vehicle_id).setLatLng([v.lat, v.lon]).setIcon(icon);
      } else {
        const m = L.marker([v.lat, v.lon], { icon })
          .bindPopup(popup, { className: 'map-popup' })
          .addTo(map);
        _markers.set(v.vehicle_id, m);
      }
    });

    // Map chip
    setText('map-count-chip', `${vehicles.length} véhicules`);

    // KPI
    const valEl  = $('kpi-vehicles-val');
    const target = vehicles.length;
    animateTo(valEl, target, 0);

    setKpi('kpi-vehicles-val', 'kpi-vehicles-fill', 'kpi-vehicles-sub', {
      value:    target,
      fillPct:  (target / MAX_VEHICLES) * 100,
      sub:      `${target} en circulation`,
    });

  } catch (e) {
    console.warn('[vehicles]', e);
  }
}

/* 3. Route delays (ranking + delay KPI) ─────────────────── */
async function loadRoutesDelays() {
  try {
    const routes = await apiFetch('/routes/delays?top=10');
    const list   = $('delay-list');

    if (!routes.length) {
      list.innerHTML = '<div class="empty-state">Aucune donnée récente (< 5 min)</div>';
      return;
    }

    const maxDelay = Math.max(...routes.map(r => r.avg_delay_seconds), 1);
    const avgDelay = routes.reduce((s, r) => s + r.avg_delay_seconds, 0) / routes.length;

    // KPI — network average delay
    const avgMin   = avgDelay / 60;
    const fillPct  = Math.min((avgDelay / 300) * 100, 100);
    let   fillCls  = '';
    let   subText  = '↓ Circulation fluide';

    if (avgDelay > 180)      { fillCls = 'fill-danger'; subText = '↑ Réseau sous pression'; }
    else if (avgDelay > 90)  { fillCls = 'fill-warn';   subText = '→ Retards modérés'; }

    const delayValEl = $('kpi-delay-val');
    if (delayValEl) {
      delayValEl.innerHTML = avgMin.toFixed(1) + '<span class="kpi-unit">min</span>';
    }
    const delayFillEl = $('kpi-delay-fill');
    if (delayFillEl) {
      delayFillEl.style.width = fillPct + '%';
      delayFillEl.className   = 'kpi-fill' + (fillCls ? ' ' + fillCls : '');
    }
    setText('kpi-delay-sub', subText);

    // Delay list
    list.innerHTML = routes.map(r => {
      const pct      = ((r.avg_delay_seconds / maxDelay) * 100).toFixed(1);
      const mins     = (r.avg_delay_seconds / 60).toFixed(1);
      const isDanger = r.avg_delay_seconds > 180;
      const isWarn   = r.avg_delay_seconds > 90 && !isDanger;

      const badgeCls  = isDanger ? 'rb-danger' : isWarn ? 'rb-warn' : '';
      const barCls    = isDanger ? 'bar-danger' : isWarn ? 'bar-warn' : '';
      const valCls    = isDanger ? 'rv-danger'  : isWarn ? 'rv-warn'  : 'rv-ok';

      return `
        <div class="delay-row">
          <div class="route-badge ${badgeCls}">${r.route_id}</div>
          <div class="route-progress-wrap">
            <div class="route-obs">${r.n_obs} obs.</div>
            <div class="route-bar">
              <div class="route-bar-fill ${barCls}" style="width:${pct}%"></div>
            </div>
          </div>
          <div class="route-delay-val ${valCls}">${mins} min</div>
        </div>`;
    }).join('');

  } catch (e) {
    console.warn('[routes/delays]', e);
  }
}

/* 4. Weather ─────────────────────────────────────────────── */
async function loadWeather() {
  try {
    const w = await apiFetch('/weather/current');

    if (!w.available) {
      setText('weather-temp', 'N/A');
      setText('weather-desc', 'Données indisponibles');
      setDot('dot-weather-sys', 'warn');
      setText('val-weather-sys', 'Indisponible');
      return;
    }

    const code = w.weather_code ?? 0;
    setText('weather-temp', `${Math.round(w.temperature_c)}°`);
    setText('weather-desc', WEATHER_DESC[code] ?? 'Conditions variables');
    setText('weather-emoji', WEATHER_ICON[code] ?? '⛅');
    setText('w-precip', `${w.precipitation_mm} mm`);
    setText('w-wind',   `${Math.round(w.wind_speed_kmh)} km/h`);

    setText('weather-time', new Date().toLocaleTimeString('fr-CA', { hour:'2-digit', minute:'2-digit' }));

    setDot('dot-weather-sys', 'ok');
    setText('val-weather-sys', 'En ligne');

    // Pre-fill predictor fields
    $('pred-temp').value   = w.temperature_c;
    $('pred-wind').value   = Math.round(w.wind_speed_kmh);
    $('pred-precip').value = w.precipitation_mm;

    const h = new Date().getHours();
    $('pred-hour').value  = h;
    $('pred-rush').value  = (h >= 7 && h <= 9) || (h >= 16 && h <= 18) ? 'true' : 'false';

  } catch (e) {
    console.warn('[weather]', e);
    setDot('dot-weather-sys', 'error');
    setText('val-weather-sys', 'Erreur');
  }
}

/* 5. Lines (for predictor select + KPI) ─────────────────── */
async function loadLines() {
  try {
    const lines = await apiFetch('/lines');
    const sel   = $('pred-route');

    sel.innerHTML = '<option value="">Choisir une ligne…</option>'
      + lines.map(l => `<option value="${l}">${l}</option>`).join('');

    setKpi('kpi-lines-val', 'kpi-lines-fill', 'kpi-lines-sub', {
      value:   lines.length,
      fillPct: Math.min((lines.length / 100) * 100, 100),
      sub:     `${lines.length} lignes avec données`,
    });

  } catch (e) {
    console.warn('[lines]', e);
  }
}

/* 6. Collector health (inferred from recent delays) ─────── */
async function checkCollector() {
  try {
    const rows = await apiFetch('/delays/live?limit=5');
    const ok   = rows.length > 0;
    setDot('dot-collector', ok ? 'ok' : 'warn');
    setText('val-collector', ok ? 'Actif' : 'Aucune donnée récente');
  } catch {
    setDot('dot-collector', 'error');
    setText('val-collector', 'Erreur');
  }
}

/* ── PREDICTOR ───────────────────────────────────────────── */
window.runPredict = async function () {
  const route = $('pred-route').value;
  if (!route) {
    // brief shake animation on select
    const sel = $('pred-route');
    sel.style.borderColor = 'var(--danger)';
    sel.style.boxShadow   = '0 0 0 2.5px var(--danger-dim)';
    setTimeout(() => { sel.style.borderColor = ''; sel.style.boxShadow = ''; }, 1200);
    return;
  }

  const btn   = $('btn-predict');
  const label = $('btn-label');
  btn.disabled      = true;
  label.textContent = 'Calcul en cours…';

  const payload = {
    route_id:         route,
    hour_of_day:      parseInt($('pred-hour').value),
    is_rush_hour:     $('pred-rush').value === 'true',
    temperature_c:    parseFloat($('pred-temp').value),
    wind_speed_kmh:   parseFloat($('pred-wind').value),
    precipitation_mm: parseFloat($('pred-precip').value),
  };

  try {
    const res = await fetch(`${BASE}/predict`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? `HTTP ${res.status}`);
    }

    const p = await res.json();

    const mins   = p.predicted_delay_minutes.toFixed(1);
    const secs   = Math.round(p.predicted_delay_seconds);
    const color  = secs > 180 ? 'var(--danger)' : secs > 90 ? 'var(--warning)' : 'var(--success)';

    const resultEl = $('predict-result');
    resultEl.classList.add('visible');

    const minEl = $('result-min');
    minEl.style.color = color;

    // Animate number
    const prev = parseFloat(minEl.dataset.current || '0');
    minEl.dataset.current = prev;
    animateTo(minEl, parseFloat(mins), 1);

    setText('result-sec',       `(${secs} s)`);
    setText('r-confidence',     { high:'Élevée', medium:'Moyenne', low:'Faible' }[p.confidence] ?? p.confidence);
    setText('r-predictor',      p.predictor);
    setText('r-obs',            p.observations ? String(p.observations) : '—');

  } catch (e) {
    alert(`Erreur de prédiction :\n${e.message}`);
  } finally {
    btn.disabled      = false;
    label.textContent = 'Calculer le délai prédit';
  }
};

/* ── TIMESTAMP ───────────────────────────────────────────── */
function updateRefreshTime() {
  const now  = new Date();
  const time = now.toLocaleTimeString('fr-CA', {
    hour:'2-digit', minute:'2-digit', second:'2-digit',
  });
  setText('last-refresh', `Mis à jour ${time}`);
}

/* ── INIT & REFRESH LOOP ─────────────────────────────────── */
async function refresh() {
  await Promise.allSettled([
    loadHealth(),
    loadVehicles(),
    loadRoutesDelays(),
    loadWeather(),
    checkCollector(),
  ]);
  updateRefreshTime();
}

// Stagger initial load for perceived performance
loadLines();          // populates select immediately

// Short delay so the page paints first, then fetch data
setTimeout(refresh, 200);
setInterval(refresh, REFRESH_MS);

// Topbar clock — tourne indépendamment toutes les secondes
setInterval(updateRefreshTime, 1_000);
