"""
Client GTFS-RT STM — Phase 1 / Semaine 1

Ce module gère la récupération des flux GTFS-RT de la STM :
  - VehiclePositions : positions GPS temps réel des bus
  - TripUpdates     : mises à jour horaires (délais aux arrêts)
  - Alerts          : perturbations de service

Documentation STM :
  https://www.stm.info/fr/a-propos/developpeurs

Utilisation rapide :
    client = GTFSClient()
    positions = client.get_vehicle_positions()
    updates   = client.get_trip_updates()
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests
from google.transit import gtfs_realtime_pb2

from src.utils.config import settings

logger = logging.getLogger(__name__)


# ─── Modèles de données simples ───────────────────────────────────────────────

@dataclass
class VehiclePosition:
    vehicle_id: str
    trip_id: str
    route_id: str
    latitude: float
    longitude: float
    bearing: Optional[float]
    speed: Optional[float]           # en m/s
    timestamp: datetime


@dataclass
class StopTimeUpdate:
    stop_id: str
    stop_sequence: int
    arrival_delay: Optional[int]     # secondes (positif = retard) — calculé dans main.py
    departure_delay: Optional[int]
    arrival_time: Optional[int]      # timestamp Unix brut depuis le feed RT


@dataclass
class TripUpdate:
    trip_id: str
    route_id: str
    start_date: str                  # "YYYYMMDD" — date de service locale
    collected_at: datetime
    stop_updates: list[StopTimeUpdate] = field(default_factory=list)


# ─── Client principal ─────────────────────────────────────────────────────────

class GTFSClient:
    """
    Récupère et décode les flux GTFS-RT de la STM.

    La STM fournit trois endpoints :
      /vehiclePositions  — protobuf FeedMessage
      /tripUpdates       — protobuf FeedMessage
      /alerts            — protobuf FeedMessage (non utilisé dans ce projet)
    """

    ENDPOINTS = {
        "vehicle_positions": "/vehiclePositions",
        "trip_updates":      "/tripUpdates",
        "alerts":            "/alerts",
    }

    def __init__(self):
        self.base_url = settings.stm_gtfs_rt_base_url
        self.headers = {
            "apiKey": settings.stm_api_key,
            "Accept": "application/x-protobuf",
        }

    def _fetch_feed(self, endpoint_key: str) -> gtfs_realtime_pb2.FeedMessage:
        """Télécharge et décode un flux protobuf GTFS-RT."""
        url = self.base_url + self.ENDPOINTS[endpoint_key]
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"[GTFS] Erreur réseau ({endpoint_key}): {e}")
            raise

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        logger.debug(f"[GTFS] {endpoint_key}: {len(feed.entity)} entités reçues")
        return feed

    def get_vehicle_positions(self) -> list[VehiclePosition]:
        """
        Retourne la liste des positions actuelles de tous les véhicules.

        Exemple de retour :
            [VehiclePosition(vehicle_id='38201', route_id='18', lat=45.55, lon=-73.60, ...), ...]
        """
        feed = self._fetch_feed("vehicle_positions")
        positions = []

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle

            # Certains véhicules n'ont pas de trip assigné
            trip_id  = vp.trip.trip_id  if vp.HasField("trip")  else ""
            route_id = vp.trip.route_id if vp.HasField("trip")  else ""

            positions.append(VehiclePosition(
                vehicle_id=vp.vehicle.id,
                trip_id=trip_id,
                route_id=route_id,
                latitude=vp.position.latitude,
                longitude=vp.position.longitude,
                bearing=vp.position.bearing if vp.position.HasField("bearing") else None,
                speed=vp.position.speed if vp.position.HasField("speed") else None,
                timestamp=datetime.fromtimestamp(vp.timestamp, tz=timezone.utc),
            ))

        logger.info(f"[GTFS] {len(positions)} positions reçues")
        return positions

    def get_trip_updates(self) -> list[TripUpdate]:
        """
        Retourne les mises à jour de délais pour tous les trajets actifs.

        Chaque TripUpdate contient une liste de StopTimeUpdate avec
        les délais d'arrivée et de départ en secondes.
        """
        feed = self._fetch_feed("trip_updates")
        updates = []
        now = datetime.now(tz=timezone.utc)

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update

            stop_updates = []
            for stu in tu.stop_time_update:
                arrival_time = stu.arrival.time if stu.HasField("arrival") else None

                stop_updates.append(StopTimeUpdate(
                    stop_id=stu.stop_id,
                    stop_sequence=stu.stop_sequence,
                    arrival_delay=None,   # calculé dans main.py à partir de arrival_time
                    departure_delay=None,
                    arrival_time=arrival_time,
                ))

            updates.append(TripUpdate(
                trip_id=tu.trip.trip_id,
                route_id=tu.trip.route_id,
                start_date=tu.trip.start_date,
                collected_at=now,
                stop_updates=stop_updates,
            ))

        logger.info(f"[GTFS] {len(updates)} trip updates reçus")
        return updates
