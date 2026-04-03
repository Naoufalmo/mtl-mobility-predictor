FROM python:3.11-slim

WORKDIR /app

# Dépendances système (libpq pour psycopg2-binary)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python (layer caché séparément du code)
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Code source
COPY src/ ./src/

# CMD surchargé par docker-compose selon le service (collector ou api)
