"""
Utilitaires de connexion à la base de données.
Utilise SQLAlchemy pour la gestion des sessions.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.utils.config import settings

# Moteur SQLAlchemy — partagé entre tous les modules
engine = create_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,      # Vérifie la connexion avant chaque utilisation
    echo=False,              # Passer à True pour déboguer les requêtes SQL
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db() -> Session:
    """Context manager pour les sessions DB — ferme automatiquement."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_connection() -> bool:
    """Vérifie que la connexion à la DB fonctionne. Utile au démarrage."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"[DB] Erreur de connexion : {e}")
        return False
