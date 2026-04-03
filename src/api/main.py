"""
API FastAPI — Phase 3 / Semaine 5

Endpoints exposés :
  GET  /               — santé de l'API
  GET  /lines          — liste des lignes STM disponibles
  GET  /delays/live    — délais temps réel depuis PostGIS
  POST /predict        — prédiction de délai pour une ligne + contexte

Usage local :
    uvicorn src.api.main:app --reload --port 8000

Puis ouvrir http://localhost:8000/docs pour la doc interactive Swagger.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.utils.config import settings
from src.utils.db import get_db, check_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

# ─── Application ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Montréal Urban Mobility Predictor",
    description=(
        "API de prédiction des délais du réseau STM.\n\n"
        "Phase 3 du projet portfolio — Polytechnique Montréal."
    ),
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Modèle et artifacts chargés en mémoire au démarrage
_model                = None
_route_encoder:       dict[str, int] = {}
_active_features:     list[str] = []
_route_hour_lookup:   dict[str, dict[str, dict]] = {}
_global_median:       float = 0.0

MODEL_PATH     = "data/features/model.pkl"
ARTIFACTS_PATH = "data/features/model_artifacts.json"


@app.on_event("startup")
def load_model():
    global _model, _route_encoder, _active_features, _route_hour_lookup, _global_median
    if Path(MODEL_PATH).exists():
        _model = joblib.load(MODEL_PATH)
        logger.info(f"Modèle chargé depuis {MODEL_PATH}")
    else:
        logger.warning(
            f"Modèle introuvable ({MODEL_PATH}). "
            "Endpoint /predict retournera 503 jusqu'à l'entraînement."
        )
    if Path(ARTIFACTS_PATH).exists():
        import json
        artifacts = json.loads(Path(ARTIFACTS_PATH).read_text())
        _route_encoder     = artifacts.get("route_encoder", {})
        _active_features   = artifacts.get("active_features", [])
        _route_hour_lookup = artifacts.get("route_hour_lookup", {})
        _global_median     = artifacts.get("global_median", 0.0)
        n_entries = sum(len(v) for v in _route_hour_lookup.values())
        logger.info(f"Artifacts chargés : {len(_route_encoder)} routes, {n_entries} entrées lookup")
    else:
        logger.warning(f"Artifacts introuvables ({ARTIFACTS_PATH}). Relancer le notebook 04.")


# ─── Schémas Pydantic ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db_ok: bool
    model_loaded: bool
    timestamp: datetime


class LiveDelay(BaseModel):
    route_id: str
    stop_id: str
    delay_seconds: int
    collected_at: datetime


class PredictRequest(BaseModel):
    route_id: str              = Field(..., example="18",  description="Identifiant de la ligne STM")
    hour_of_day: int           = Field(..., ge=0, le=23,   example=8,    description="Heure locale (0-23)")
    is_rush_hour: bool         = Field(True,               example=True,  description="True si heure de pointe (7-9h ou 16-18h)")
    temperature_c: float       = Field(...,                example=5.0,   description="Température en °C")
    wind_speed_kmh: float      = Field(...,                example=20.0,  description="Vitesse du vent en km/h")
    precipitation_mm: float    = Field(0.0,                example=0.0,   description="Précipitations en mm (0 = pas de pluie/neige)")
    day_of_week: Optional[int] = Field(None, ge=0, le=6,  example=None,  description="Optionnel — sera utile avec collecte multi-jours")
    week_of_year: Optional[int]= Field(None, ge=1, le=52, example=None,  description="Optionnel — sera utile avec collecte multi-jours")


class PredictResponse(BaseModel):
    route_id: str
    predicted_delay_seconds: float
    predicted_delay_minutes: float
    confidence: str        # "low" | "medium" | "high"
    predictor: str         # "lookup" | "xgboost" | "fallback"
    observations: int      # nb d'obs historiques pour cette route×heure
    model_version: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Santé"])
def health():
    """Vérifie l'état de l'API, de la DB et du modèle."""
    return HealthResponse(
        status="ok",
        db_ok=check_connection(),
        model_loaded=_model is not None,
        timestamp=datetime.now(tz=timezone.utc),
    )


@app.get("/lines", response_model=list[str], tags=["Données"])
def get_lines():
    """
    Retourne la liste des route_id pour lesquels des données ont été collectées.
    """
    try:
        with get_db() as db:
            rows = db.execute(
                text("SELECT DISTINCT route_id FROM stop_delays ORDER BY route_id")
            ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB indisponible : {e}")


@app.get("/delays/live", response_model=list[LiveDelay], tags=["Données"])
def get_live_delays(route_id: Optional[str] = None, limit: int = 100):
    """
    Retourne les derniers délais collectés.

    - **route_id** : filtrer par ligne (ex: `18`)
    - **limit** : nombre de résultats (max 500)
    """
    limit = min(limit, 500)
    base_query = """
        SELECT route_id, stop_id, delay_seconds, collected_at
        FROM stop_delays
        {where}
        ORDER BY collected_at DESC
        LIMIT :limit
    """
    where = "WHERE route_id = :route_id" if route_id else ""
    params = {"limit": limit}
    if route_id:
        params["route_id"] = route_id

    try:
        with get_db() as db:
            rows = db.execute(text(base_query.format(where=where)), params).fetchall()
        return [
            LiveDelay(
                route_id=r.route_id,
                stop_id=r.stop_id,
                delay_seconds=r.delay_seconds,
                collected_at=r.collected_at,
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB indisponible : {e}")


@app.post("/predict", response_model=PredictResponse, tags=["Prédiction"])
def predict(req: PredictRequest):
    """
    Prédit le délai en secondes pour une ligne STM donnée.

    **Stratégie de prédiction (en cascade) :**
    1. `lookup` — médiane historique par `route × heure` (source principale avec 1 journée de données)
    2. `xgboost` — modèle ML si la route est hors du lookup
    3. `fallback` — médiane globale en dernier recours

    Le champ `predictor` dans la réponse indique quelle stratégie a été utilisée.
    Le champ `observations` indique le nombre d'observations historiques derrière la prédiction.
    """
    if not _route_hour_lookup and _model is None:
        raise HTTPException(
            status_code=503,
            detail="Aucun artifact chargé. Relancer le notebook 04 puis redémarrer l'API.",
        )

    predicted: float
    predictor: str
    n_obs: int = 0

    # ── Stratégie 1 : lookup route × heure (fiable avec 1 journée de données) ──
    route_data = _route_hour_lookup.get(str(req.route_id))
    if route_data is not None:
        hour_data = route_data.get(str(req.hour_of_day))
        if hour_data is not None:
            predicted  = float(hour_data["median"])
            n_obs      = int(hour_data["n"])
            predictor  = "lookup"
        else:
            # Route connue mais heure non couverte → médiane de la route
            all_medians = [v["median"] for v in route_data.values()]
            predicted   = float(np.median(all_medians))
            predictor   = "lookup-route-only"
    elif _model is not None and _active_features:
        # ── Stratégie 2 : XGBoost (quand la route est dans l'encodeur) ──────────
        route_enc = _route_encoder.get(str(req.route_id))
        if route_enc is None:
            raise HTTPException(
                status_code=422,
                detail=f"route_id '{req.route_id}' inconnue. Lignes connues : {sorted(_route_hour_lookup.keys())}",
            )
        all_features = {
            "route_id_encoded": route_enc,
            "hour_of_day":      req.hour_of_day,
            "day_of_week":      req.day_of_week,
            "week_of_year":     req.week_of_year,
            "is_rush_hour":     int(req.is_rush_hour),
            "temperature_c":    req.temperature_c,
            "precipitation_mm": req.precipitation_mm,
            "wind_speed_kmh":   req.wind_speed_kmh,
            "is_precipitation": int(req.precipitation_mm > 0.1),
        }
        features  = pd.DataFrame([[all_features[f] for f in _active_features]], columns=_active_features)
        predicted = float(np.clip(_model.predict(features)[0], 0, 3600))
        predictor = "xgboost"
    else:
        # ── Stratégie 3 : médiane globale ────────────────────────────────────────
        predicted = _global_median
        predictor = "fallback"

    confidence = (
        "high"   if n_obs >= 100 else
        "medium" if n_obs >= 20  else
        "low"
    )

    return PredictResponse(
        route_id=req.route_id,
        predicted_delay_seconds=round(predicted, 1),
        predicted_delay_minutes=round(predicted / 60, 2),
        confidence=confidence,
        predictor=predictor,
        observations=n_obs,
        model_version=_artifacts_version(),
    )


def _artifacts_version() -> str:
    try:
        import json
        return json.loads(Path(ARTIFACTS_PATH).read_text()).get("model_version", "unknown")
    except Exception:
        return "unknown"


# ─── Frontend & endpoints carte ───────────────────────────────────────────────

@app.get("/", include_in_schema=False)
@app.get("/map", include_in_schema=False)
def serve_map():
    return FileResponse("frontend/index.html")


@app.get("/vehicles/live", tags=["Carte"])
def get_live_vehicles(minutes: int = 3, limit: int = 500):
    """
    Positions GPS des bus actifs + délai moyen de leur ligne.
    Utilisé par la carte Leaflet.
    """
    try:
        with get_db() as db:
            rows = db.execute(text("""
                WITH latest_pos AS (
                    SELECT DISTINCT ON (vehicle_id)
                        vehicle_id,
                        route_id,
                        ST_Y(location::geometry) AS lat,
                        ST_X(location::geometry) AS lon,
                        bearing,
                        collected_at
                    FROM vehicle_positions
                    WHERE collected_at > NOW() - (:minutes * INTERVAL '1 minute')
                      AND location IS NOT NULL
                    ORDER BY vehicle_id, collected_at DESC
                ),
                recent_delays AS (
                    SELECT route_id, ROUND(AVG(delay_seconds)) AS avg_delay
                    FROM stop_delays
                    WHERE collected_at > NOW() - INTERVAL '5 minutes'
                      AND delay_seconds BETWEEN -600 AND 3600
                    GROUP BY route_id
                )
                SELECT
                    lp.vehicle_id,
                    lp.route_id,
                    lp.lat,
                    lp.lon,
                    lp.bearing,
                    COALESCE(rd.avg_delay, 0) AS avg_delay_seconds
                FROM latest_pos lp
                LEFT JOIN recent_delays rd ON rd.route_id = lp.route_id
                ORDER BY lp.route_id
                LIMIT :limit
            """), {"limit": limit, "minutes": minutes}).fetchall()
        return [
            {
                "vehicle_id":       r.vehicle_id,
                "route_id":         r.route_id,
                "lat":              r.lat,
                "lon":              r.lon,
                "bearing":          r.bearing,
                "avg_delay_seconds": int(r.avg_delay_seconds),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB indisponible : {e}")


@app.get("/weather/current", tags=["Carte"])
def get_current_weather():
    """Dernier snapshot météo disponible."""
    try:
        with get_db() as db:
            row = db.execute(text("""
                SELECT temperature_c, precipitation_mm, wind_speed_kmh,
                       weather_code, is_precipitation, collected_at
                FROM weather_snapshots
                ORDER BY collected_at DESC
                LIMIT 1
            """)).fetchone()
        if row is None:
            return {"available": False}
        return {
            "available":        True,
            "temperature_c":    row.temperature_c,
            "precipitation_mm": row.precipitation_mm,
            "wind_speed_kmh":   row.wind_speed_kmh,
            "weather_code":     row.weather_code,
            "is_precipitation": row.is_precipitation,
            "collected_at":     row.collected_at.isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB indisponible : {e}")


@app.get("/routes/delays", tags=["Carte"])
def get_routes_delays(top: int = 10):
    """Délai moyen par ligne sur les 5 dernières minutes, trié par retard décroissant."""
    try:
        with get_db() as db:
            rows = db.execute(text("""
                SELECT route_id,
                       ROUND(AVG(delay_seconds))  AS avg_delay,
                       COUNT(*)                   AS n_obs
                FROM stop_delays
                WHERE collected_at > NOW() - INTERVAL '5 minutes'
                  AND delay_seconds BETWEEN -600 AND 3600
                GROUP BY route_id
                HAVING COUNT(*) >= 3
                ORDER BY avg_delay DESC
                LIMIT :top
            """), {"top": top}).fetchall()
        return [
            {"route_id": r.route_id, "avg_delay_seconds": int(r.avg_delay), "n_obs": r.n_obs}
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"DB indisponible : {e}")
