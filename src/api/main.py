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

# Servir le frontend statique (Leaflet.js) — Phase 3
# app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Modèle chargé en mémoire au démarrage
_model = None
MODEL_PATH = "data/features/model.pkl"


@app.on_event("startup")
def load_model():
    global _model
    if Path(MODEL_PATH).exists():
        _model = joblib.load(MODEL_PATH)
        logger.info(f"Modèle chargé depuis {MODEL_PATH}")
    else:
        logger.warning(
            f"Modèle introuvable ({MODEL_PATH}). "
            "Endpoint /predict retournera 503 jusqu'à l'entraînement."
        )


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
    route_id: str = Field(..., example="18", description="Identifiant de la ligne STM")
    hour_of_day: int = Field(..., ge=0, le=23, example=8)
    day_of_week: int = Field(..., ge=0, le=6,  example=1, description="0=dim, 6=sam")
    week_of_year: int = Field(..., ge=1, le=52, example=15)
    is_rush_hour: bool = Field(True, example=True)
    temperature_c: float = Field(..., example=5.0)
    precipitation_mm: float = Field(0.0, example=0.0)
    wind_speed_kmh: float = Field(..., example=20.0)


class PredictResponse(BaseModel):
    route_id: str
    predicted_delay_seconds: float
    predicted_delay_minutes: float
    confidence: str       # "low" | "medium" | "high" (heuristique simple)
    model_version: str


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", response_model=HealthResponse, tags=["Santé"])
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
    Prédit le délai en secondes pour une ligne donnée dans un contexte donné.

    Le modèle XGBoost doit avoir été entraîné au préalable (Phase 2).
    """
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail="Modèle non chargé. Lancez d'abord : python -m src.models.train",
        )

    # Construire le vecteur de features dans le même ordre que l'entraînement
    # Note : route_id_encoded est une simplification — en prod, utiliser
    #        le même encodeur LabelEncoder sauvegardé avec le modèle
    features = pd.DataFrame([{
        "route_id_encoded": hash(req.route_id) % 1000,  # TODO : charger l'encodeur réel
        "hour_of_day":      req.hour_of_day,
        "day_of_week":      req.day_of_week,
        "week_of_year":     req.week_of_year,
        "is_rush_hour":     int(req.is_rush_hour),
        "temperature_c":    req.temperature_c,
        "precipitation_mm": req.precipitation_mm,
        "wind_speed_kmh":   req.wind_speed_kmh,
        "is_precipitation": int(req.precipitation_mm > 0.1),
    }])

    predicted = float(_model.predict(features)[0])

    # Heuristique de confiance (à affiner avec des intervalles de prédiction)
    confidence = (
        "high"   if abs(predicted) < 60   else
        "medium" if abs(predicted) < 300  else
        "low"
    )

    return PredictResponse(
        route_id=req.route_id,
        predicted_delay_seconds=round(predicted, 1),
        predicted_delay_minutes=round(predicted / 60, 2),
        confidence=confidence,
        model_version="xgboost-v0.1",
    )
