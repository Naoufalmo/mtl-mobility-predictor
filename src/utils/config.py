"""
Configuration centralisée via pydantic-settings.
Charge automatiquement les variables depuis le fichier .env
"""

from pydantic_settings import BaseSettings
from pydantic import computed_field


class Settings(BaseSettings):
    # STM
    stm_api_key: str = "l7b7959bd7440e4aa896a73202263092fd"
    stm_gtfs_rt_base_url: str = "https://api.stm.info/pub/od/gtfs-rt/ic/v2"

    # Météo
    weather_api_url: str = "https://api.open-meteo.com/v1/forecast"
    mtl_latitude: float = 45.5017
    mtl_longitude: float = -73.5673

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "mobility"
    postgres_user: str = "mobility_user"
    postgres_password: str = "changeme"

    # Collecte
    collection_interval_sec: int = 30

    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment_name: str = "stm-delay-prediction"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Instance globale importable partout : from src.utils.config import settings
settings = Settings()
