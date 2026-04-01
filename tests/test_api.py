"""
Tests unitaires FastAPI — Phase 3

Utilise httpx comme client de test (pas besoin de démarrer le serveur).
La DB et le modèle sont mockés pour que les tests soient rapides et isolés.

Usage :
    pytest tests/test_api.py -v
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


# ─── /health ──────────────────────────────────────────────────────────────────

def test_health_returns_200():
    with patch("src.api.main.check_connection", return_value=True):
        resp = client.get("/")
    assert resp.status_code == 200


def test_health_schema():
    with patch("src.api.main.check_connection", return_value=False):
        data = client.get("/").json()
    assert "status" in data
    assert "db_ok" in data
    assert "model_loaded" in data


# ─── /predict ─────────────────────────────────────────────────────────────────

VALID_PAYLOAD = {
    "route_id":        "18",
    "hour_of_day":     8,
    "day_of_week":     1,
    "week_of_year":    15,
    "is_rush_hour":    True,
    "temperature_c":   5.0,
    "precipitation_mm": 0.0,
    "wind_speed_kmh":  20.0,
}


def test_predict_without_model_returns_503():
    """Sans modèle chargé, l'API doit retourner 503."""
    with patch("src.api.main._model", None):
        resp = client.post("/predict", json=VALID_PAYLOAD)
    assert resp.status_code == 503


def test_predict_with_mock_model_returns_200():
    """Avec un modèle mocké, l'endpoint doit retourner une prédiction valide."""
    mock_model = MagicMock()
    mock_model.predict.return_value = [120.0]   # 120 secondes de retard

    with patch("src.api.main._model", mock_model):
        resp = client.post("/predict", json=VALID_PAYLOAD)

    assert resp.status_code == 200
    data = resp.json()
    assert data["route_id"] == "18"
    assert data["predicted_delay_seconds"] == 120.0
    assert data["predicted_delay_minutes"] == 2.0
    assert data["confidence"] in ("low", "medium", "high")


def test_predict_invalid_hour_returns_422():
    """Heure invalide (>23) doit retourner une erreur de validation."""
    payload = {**VALID_PAYLOAD, "hour_of_day": 25}
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 422


def test_predict_missing_field_returns_422():
    """Payload incomplet doit retourner une erreur de validation."""
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "route_id"}
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 422
