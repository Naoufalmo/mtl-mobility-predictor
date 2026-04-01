"""
Entraînement XGBoost — Phase 2 / Semaine 4

Ce script :
  1. Charge le dataset Parquet (produit par features.py)
  2. Effectue une validation croisée temporelle (TimeSeriesSplit)
  3. Entraîne un XGBoost avec les meilleurs hyperparamètres trouvés
  4. Logue les métriques et le modèle dans MLflow
  5. Sauvegarde le modèle sérialisé en data/features/model.pkl

Usage :
    python -m src.models.train

Pré-requis :
    - data/features/dataset.parquet existe (généré par features.py)
    - MLflow UI lancé : docker-compose up mlflow
"""

import logging
from pathlib import Path

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit
from xgboost import XGBRegressor

from src.models.features import FEATURE_COLUMNS, TARGET_COLUMN, load_feature_dataset
from src.utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train")

DATASET_PATH  = "data/features/dataset.parquet"
MODEL_PATH    = "data/features/model.pkl"
N_SPLITS      = 5   # Nombre de folds pour TimeSeriesSplit


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Calcule MAE, RMSE et MAPE."""
    mae  = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))

    # MAPE : éviter division par zéro
    mask = y_true != 0
    mape = np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100

    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def train():
    logger.info("=" * 55)
    logger.info("  Entraînement XGBoost — Delay Predictor")
    logger.info("=" * 55)

    # ── 1. Charger le dataset ─────────────────────────────────────────────────
    if not Path(DATASET_PATH).exists():
        raise FileNotFoundError(
            f"Dataset introuvable : {DATASET_PATH}\n"
            "Générez-le d'abord avec : python -m src.models.features"
        )

    X, y = load_feature_dataset(DATASET_PATH)
    logger.info(f"Dataset : {len(X)} lignes × {len(FEATURE_COLUMNS)} features")

    # ── 2. Baseline naïve (médiane globale) ───────────────────────────────────
    baseline_pred = np.full(len(y), y.median())
    baseline_metrics = evaluate(y.values, baseline_pred)
    logger.info(f"Baseline (médiane) — MAE: {baseline_metrics['MAE']:.1f}s")

    # ── 3. Validation croisée temporelle ──────────────────────────────────────
    # ⚠️  TimeSeriesSplit respecte l'ordre chronologique.
    #     Jamais de shuffle ici — ce serait du data leakage !
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)

    cv_maes = []
    model = XGBRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
    )

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
        preds = model.predict(X_val)
        mae = mean_absolute_error(y_val, preds)
        cv_maes.append(mae)
        logger.info(f"  Fold {fold + 1}/{N_SPLITS} — MAE: {mae:.1f}s")

    mean_cv_mae = np.mean(cv_maes)
    logger.info(f"CV MAE moyen : {mean_cv_mae:.1f}s (baseline : {baseline_metrics['MAE']:.1f}s)")

    # ── 4. Entraînement final sur tout le dataset ──────────────────────────────
    model.fit(X, y, verbose=False)

    # ── 5. Logging MLflow ─────────────────────────────────────────────────────
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment_name)

    with mlflow.start_run(run_name="xgboost-baseline"):
        # Hyperparamètres
        mlflow.log_params({
            "n_estimators":    300,
            "max_depth":       6,
            "learning_rate":   0.05,
            "subsample":       0.8,
            "colsample_bytree": 0.8,
            "n_splits":        N_SPLITS,
            "n_rows":          len(X),
        })

        # Métriques
        mlflow.log_metrics({
            "cv_mae_mean":     mean_cv_mae,
            "baseline_mae":    baseline_metrics["MAE"],
            "baseline_rmse":   baseline_metrics["RMSE"],
        })

        # Feature importances (utile pour la phase d'analyse)
        importances = dict(zip(FEATURE_COLUMNS, model.feature_importances_))
        for feat, imp in sorted(importances.items(), key=lambda x: -x[1]):
            logger.info(f"  {feat:<25} {imp:.4f}")
            mlflow.log_metric(f"fi_{feat}", float(imp))

        # Modèle sérialisé
        mlflow.sklearn.log_model(model, artifact_path="model")

    # ── 6. Sauvegarde locale ───────────────────────────────────────────────────
    Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    logger.info(f"Modèle sauvegardé → {MODEL_PATH}")
    logger.info("Entraînement terminé ✓")


if __name__ == "__main__":
    train()
