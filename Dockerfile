# ==============================================================================
# Dockerfile - Garmin Connect AI Coach Backend
# ==============================================================================
FROM python:3.12-slim

# Evita la scrittura dei file .pyc e abilita l'unbuffered output per i log in tempo reale
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Installa dipendenze di sistema minime
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia e installa i requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'applicazione
COPY . .

# Crea la cartella per i token se non esiste
RUN mkdir -p /app/tokens

# Esponi la porta del server
EXPOSE 8000

# Health check per container orchestrator (Docker, AWS ECS, Railway, ecc.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Comando di avvio Uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
