"""
Collecteur principal — Phase 2 / Semaine 2

Lance deux jobs APScheduler en parallèle :
  - Toutes les 30 s : positions GTFS-RT + délais aux arrêts → PostGIS
  - Toutes les 10 min : snapshot météo → PostGIS

Usage :
    python -m src.collector.main

Ou via Docker Compose (décommenter le service 'collector' dans docker-compose.yml).
"""

import logging
import signal
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from src.utils.config import settings
from src.utils.db import get_db, check_connection
from src.collector.gtfs_client import GTFSClient
from src.collector.weather_client import get_current_weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("collector")

gtfs_client = GTFSClient()


# ─── Jobs ─────────────────────────────────────────────────────────────────────

def collect_vehicle_positions() -> None:
    """
    Récupère les positions GPS de tous les véhicules STM
    et les insère dans vehicle_positions.

    TODO Phase 2 : connecter à la vraie DB via get_db()
    Pour l'instant, on affiche juste le count pour valider la connexion API.
    """
    try:
        positions = gtfs_client.get_vehicle_positions()
        logger.info(f"[Positions] {len(positions)} véhicules actifs")

        # ── ÉTAPE 2 : Insérer en base ─────────────────────────────────────────
        # Décommenter quand la DB est prête (Semaine 2) :
        
        with get_db() as db:
            for pos in positions:
                db.execute(text("""
                    INSERT INTO vehicle_positions
                        (vehicle_id, trip_id, route_id, location, bearing, speed, timestamp)
                    VALUES
                        (:vid, :tid, :rid,
                         ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
                         :bearing, :speed, :ts)
                """), {
                    "vid": pos.vehicle_id,
                    "tid": pos.trip_id,
                    "rid": pos.route_id,
                    "lat": pos.latitude,
                    "lon": pos.longitude,
                    "bearing": pos.bearing,
                    "speed": pos.speed,
                    "ts": pos.timestamp,
                })
        logger.info(f"[Positions] {len(positions)} lignes insérées en DB")

    except Exception as e:
        logger.error(f"[Positions] Échec : {e}")


def collect_trip_updates() -> None:
    """
    Récupère les délais aux arrêts depuis TripUpdates GTFS-RT
    et les insère dans stop_delays.

    TODO Phase 2 : connecter à la vraie DB via get_db()
    """
    try:
        updates = gtfs_client.get_trip_updates()
        total_stops = sum(len(u.stop_updates) for u in updates)
        logger.info(f"[TripUpdates] {len(updates)} trips, {total_stops} arrêts mis à jour")

        # ── ÉTAPE 2 : Insérer en base ─────────────────────────────────────────
        with get_db() as db:
            for update in updates:
                for stu in update.stop_updates:
                    db.execute(text("""
                        INSERT INTO stop_delays
                            (trip_id, route_id, stop_id, stop_sequence, delay_seconds)
                        VALUES (:tid, :rid, :sid, :seq, :delay)
                    """), {
                        "tid": update.trip_id,
                        "rid": update.route_id,
                        "sid": stu.stop_id,
                        "seq": stu.stop_sequence,
                        "delay": stu.arrival_delay or stu.departure_delay or 0,
                    })

    except Exception as e:
        logger.error(f"[TripUpdates] Échec : {e}")


def collect_weather() -> None:
    """
    Récupère les conditions météo actuelles et les insère dans weather_snapshots.

    TODO Phase 2 : connecter à la vraie DB via get_db()
    """
    try:
        snap = get_current_weather()
        logger.info(
            f"[Météo] {snap.temperature_c}°C | {snap.description} | "
            f"{snap.precipitation_mm}mm pluie"
        )

        # ── ÉTAPE 2 : Insérer en base ─────────────────────────────────────────
        with get_db() as db:
            db.execute(text("""
                INSERT INTO weather_snapshots
                    (temperature_c, precipitation_mm, wind_speed_kmh, weather_code)
                VALUES (:temp, :precip, :wind, :code)
            """), {
                "temp":   snap.temperature_c,
                "precip": snap.precipitation_mm,
                "wind":   snap.wind_speed_kmh,
                "code":   snap.weather_code,
            })

    except Exception as e:
        logger.error(f"[Météo] Échec : {e}")


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("  Montréal Urban Mobility Predictor — Collecteur")
    logger.info("=" * 60)

    if not check_connection():
        logger.error("Impossible de se connecter à la DB. Vérifiez docker-compose.")
        sys.exit(1)
    logger.info("[DB] Connexion PostGIS OK")

    scheduler = BlockingScheduler(timezone="America/Montreal")

    # Job 1 : positions GPS toutes les 30 secondes
    scheduler.add_job(
        collect_vehicle_positions,
        "interval",
        seconds=settings.collection_interval_sec,
        id="vehicle_positions",
        next_run_time=None,   # Démarrage immédiat au lancement du scheduler
    )

    # Job 2 : trip updates toutes les 30 secondes
    scheduler.add_job(
        collect_trip_updates,
        "interval",
        seconds=settings.collection_interval_sec,
        id="trip_updates",
    )

    # Job 3 : météo toutes les 10 minutes
    scheduler.add_job(
        collect_weather,
        "interval",
        minutes=10,
        id="weather",
    )

    def shutdown(signum, frame):
        logger.info("Signal reçu — arrêt propre du scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info(
        f"Scheduler démarré — collecte toutes les "
        f"{settings.collection_interval_sec}s (positions) / 10min (météo)"
    )

    # Premier appel immédiat pour valider les connexions API
    collect_weather()
    collect_vehicle_positions()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
