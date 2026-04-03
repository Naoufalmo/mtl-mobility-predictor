"""
Feature engineering — Phase 2 / Semaine 3

Ce module transforme les données brutes (stop_delays + weather_snapshots)
en features prêtes pour l'entraînement XGBoost/Prophet.

Colonnes produites :
  route_id         — identifiant de la ligne (encodé)
  hour_of_day      — heure (0–23)
  day_of_week      — jour (0=dim, 6=sam)
  week_of_year     — semaine (1–52)
  is_rush_hour     — True si 7h–9h ou 16h–18h
  temperature_c    — température Celsius
  precipitation_mm — pluie/neige en mm
  wind_speed_kmh   — vent en km/h
  is_precipitation — True si precipitation_mm > 0.1
  delay_seconds    — TARGET : délai en secondes

Usage :
    from src.models.features import build_feature_dataset
    df = build_feature_dataset()
    df.to_parquet("data/features/dataset.parquet")
"""

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from src.utils.db import get_db

logger = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "route_id_encoded",
    "hour_of_day",
    "day_of_week",
    "week_of_year",
    "is_rush_hour",
    "temperature_c",
    "precipitation_mm",
    "wind_speed_kmh",
    "is_precipitation",
]
TARGET_COLUMN = "delay_seconds"


def build_feature_dataset(output_path: str | None = None) -> pd.DataFrame:
    """
    Lit la vue v_delays_enriched depuis PostGIS et retourne un DataFrame propre.

    Args:
        output_path: si fourni, exporte le dataset en Parquet à ce chemin.

    Returns:
        DataFrame avec FEATURE_COLUMNS + TARGET_COLUMN, sans NaN.
    """
    query = """
        SELECT
            route_id,
            hour_of_day,
            day_of_week,
            week_of_year,
            is_rush_hour,
            temperature_c,
            precipitation_mm,
            wind_speed_kmh,
            is_precipitation,
            delay_seconds
        FROM v_delays_enriched
        WHERE delay_seconds IS NOT NULL
          AND delay_seconds != 0           -- exclure les arrêts non encore actualisés par la STM
          AND temperature_c IS NOT NULL    -- lignes sans snapshot météo associé
        ORDER BY scheduled_at
    """

    logger.info("[Features] Lecture depuis v_delays_enriched...")
    with get_db() as db:
        df = pd.read_sql(text(query), db.bind)

    logger.info(f"[Features] {len(df)} lignes brutes chargées")

    # Encoder route_id en entier
    df["route_id_encoded"] = df["route_id"].astype("category").cat.codes

    # Convertir les booléens PostgreSQL → int (XGBoost préfère 0/1)
    df["is_rush_hour"]     = df["is_rush_hour"].astype(int)
    df["is_precipitation"] = df["is_precipitation"].astype(int)

    # Supprimer les outliers extrêmes (>1h de retard = données corrompues)
    before = len(df)
    df = df[df["delay_seconds"].between(-600, 3600)]
    logger.info(f"[Features] {before - len(df)} outliers supprimés (|delay| > 1h)")

    # Supprimer les NaN restants
    df = df.dropna(subset=FEATURE_COLUMNS + [TARGET_COLUMN])

    # Échantillonner si trop volumineux pour l'entraînement (garder l'ordre chronologique)
    MAX_ROWS = 2_000_000
    if len(df) > MAX_ROWS:
        df = df.iloc[::len(df) // MAX_ROWS].head(MAX_ROWS)
        logger.info(f"[Features] Échantillonné à {MAX_ROWS:,} lignes (ordre chronologique conservé)")

    logger.info(f"[Features] {len(df)} lignes finales")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(output_path, index=False)
        logger.info(f"[Features] Dataset exporté → {output_path}")

    return df


def load_feature_dataset(parquet_path: str) -> tuple[pd.DataFrame, pd.Series]:
    """
    Charge un dataset Parquet et sépare X / y.

    Returns:
        X: DataFrame des features
        y: Series de la cible (delay_seconds)
    """
    df = pd.read_parquet(parquet_path)
    X = df[FEATURE_COLUMNS]
    y = df[TARGET_COLUMN]
    return X, y
