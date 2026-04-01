-- =============================================================================
-- Schéma PostGIS — Montréal Urban Mobility Predictor
-- Exécuté automatiquement au premier démarrage du conteneur PostgreSQL
-- =============================================================================

-- Extension géospatiale (fournie par l'image postgis/postgis)
CREATE EXTENSION IF NOT EXISTS postgis;

-- ─── Lignes de bus ────────────────────────────────────────────────────────────
-- Données statiques issues du GTFS : chargées en Phase 1 (notebook d'exploration)
CREATE TABLE IF NOT EXISTS routes (
    route_id        TEXT PRIMARY KEY,
    route_short_name TEXT,          -- ex : "18", "80", "165"
    route_long_name  TEXT,          -- ex : "Beaubien"
    route_type       SMALLINT,      -- 3 = bus
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Arrêts ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stops (
    stop_id     TEXT PRIMARY KEY,
    stop_name   TEXT,
    location    GEOMETRY(Point, 4326),   -- PostGIS point WGS84
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS stops_location_idx ON stops USING GIST (location);

-- ─── Positions temps réel des véhicules ───────────────────────────────────────
-- Alimentée par src/collector/gtfs_collector.py toutes les 30 secondes
CREATE TABLE IF NOT EXISTS vehicle_positions (
    id              BIGSERIAL PRIMARY KEY,
    vehicle_id      TEXT,
    trip_id         TEXT,
    route_id        TEXT REFERENCES routes(route_id) ON DELETE SET NULL,
    location        GEOMETRY(Point, 4326),
    bearing         REAL,           -- direction en degrés
    speed           REAL,           -- m/s
    timestamp       TIMESTAMPTZ,    -- horodatage fourni par STM
    collected_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vp_route_idx   ON vehicle_positions (route_id);
CREATE INDEX IF NOT EXISTS vp_ts_idx      ON vehicle_positions (timestamp);
CREATE INDEX IF NOT EXISTS vp_loc_idx     ON vehicle_positions USING GIST (location);

-- ─── Délais aux arrêts ────────────────────────────────────────────────────────
-- Calculés à partir des TripUpdates GTFS-RT
-- delay_seconds > 0 = en retard, < 0 = en avance
CREATE TABLE IF NOT EXISTS stop_delays (
    id              BIGSERIAL PRIMARY KEY,
    trip_id         TEXT,
    route_id        TEXT,
    stop_id         TEXT,
    stop_sequence   SMALLINT,
    scheduled_at    TIMESTAMPTZ,    -- heure prévue (GTFS statique)
    delay_seconds   INTEGER,        -- retard mesuré
    collected_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS sd_route_ts_idx ON stop_delays (route_id, collected_at);

-- ─── Snapshots météo ──────────────────────────────────────────────────────────
-- Une ligne par collecte (toutes les 30 min environ)
CREATE TABLE IF NOT EXISTS weather_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    collected_at        TIMESTAMPTZ DEFAULT NOW(),
    temperature_c       REAL,
    precipitation_mm    REAL,       -- pluie/neige sur l'heure
    wind_speed_kmh      REAL,
    weather_code        SMALLINT,   -- WMO Weather Code (0=clair, 71=neige, etc.)
    is_precipitation    BOOLEAN GENERATED ALWAYS AS (precipitation_mm > 0.1) STORED
);

-- ─── Vue utilitaire : délais enrichis (Phase 3 — feature engineering) ─────────
-- Cette vue sera utilisée par l'API pour construire les features à la volée
CREATE OR REPLACE VIEW v_delays_enriched AS
SELECT
    sd.id,
    sd.route_id,
    sd.stop_id,
    sd.scheduled_at,
    sd.delay_seconds,
    EXTRACT(HOUR   FROM sd.scheduled_at AT TIME ZONE 'America/Montreal') AS hour_of_day,
    EXTRACT(DOW    FROM sd.scheduled_at AT TIME ZONE 'America/Montreal') AS day_of_week,  -- 0=dim, 6=sam
    EXTRACT(WEEK   FROM sd.scheduled_at AT TIME ZONE 'America/Montreal') AS week_of_year,
    CASE
        WHEN EXTRACT(HOUR FROM sd.scheduled_at AT TIME ZONE 'America/Montreal')
            BETWEEN 7 AND 9 THEN TRUE
        WHEN EXTRACT(HOUR FROM sd.scheduled_at AT TIME ZONE 'America/Montreal')
            BETWEEN 16 AND 18 THEN TRUE
        ELSE FALSE
    END AS is_rush_hour,
    w.temperature_c,
    w.precipitation_mm,
    w.wind_speed_kmh,
    w.is_precipitation
FROM stop_delays sd
LEFT JOIN LATERAL (
    -- Météo la plus proche dans le temps (fenêtre ±30 min)
    SELECT * FROM weather_snapshots ws
    WHERE ws.collected_at BETWEEN sd.collected_at - INTERVAL '30 minutes'
                               AND sd.collected_at + INTERVAL '30 minutes'
    ORDER BY ABS(EXTRACT(EPOCH FROM (ws.collected_at - sd.collected_at)))
    LIMIT 1
) w ON TRUE;

-- Message de confirmation
DO $$ BEGIN
  RAISE NOTICE '✓ Schéma mobility créé avec succès (PostGIS activé)';
END $$;
