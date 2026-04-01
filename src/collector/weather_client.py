"""
Client Open-Meteo — Phase 1 / Semaine 2

Open-Meteo est une API météo gratuite, sans inscription ni clé API.
Documentation : https://open-meteo.com/en/docs

Ce module récupère les conditions météo actuelles pour Montréal
et les enregistre dans la table weather_snapshots.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from src.utils.config import settings

logger = logging.getLogger(__name__)

# Codes WMO simplifiés pour référence rapide
WMO_DESCRIPTIONS = {
    0:  "Ciel dégagé",
    1:  "Principalement dégagé",
    2:  "Partiellement nuageux",
    3:  "Couvert",
    51: "Bruine légère",
    61: "Pluie légère",
    63: "Pluie modérée",
    65: "Pluie forte",
    71: "Neige légère",
    73: "Neige modérée",
    75: "Neige forte",
    80: "Averses légères",
    95: "Orage",
}


@dataclass
class WeatherSnapshot:
    collected_at: datetime
    temperature_c: float
    precipitation_mm: float     # mm sur l'heure
    wind_speed_kmh: float
    weather_code: int
    description: str            # description humaine du code WMO


def get_current_weather() -> WeatherSnapshot:
    """
    Récupère les conditions météo actuelles à Montréal via Open-Meteo.

    Paramètres API utilisés :
      - current=temperature_2m,precipitation,windspeed_10m,weathercode
      - temperature_unit=celsius
      - windspeed_unit=kmh
      - timezone=America/Montreal

    Retourne un WeatherSnapshot prêt à insérer en base.
    """
    params = {
        "latitude":        settings.mtl_latitude,
        "longitude":       settings.mtl_longitude,
        "current":         "temperature_2m,precipitation,windspeed_10m,weathercode",
        "temperature_unit": "celsius",
        "windspeed_unit":  "kmh",
        "timezone":        "America/Montreal",
    }

    try:
        resp = requests.get(settings.weather_api_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error(f"[Weather] Erreur API : {e}")
        raise

    current = data["current"]
    code = current["weathercode"]

    snapshot = WeatherSnapshot(
        collected_at=datetime.now(tz=timezone.utc),
        temperature_c=current["temperature_2m"],
        precipitation_mm=current["precipitation"],
        wind_speed_kmh=current["windspeed_10m"],
        weather_code=code,
        description=WMO_DESCRIPTIONS.get(code, f"Code WMO {code}"),
    )

    logger.info(
        f"[Weather] {snapshot.temperature_c}°C | "
        f"{snapshot.precipitation_mm}mm | "
        f"{snapshot.description}"
    )
    return snapshot
