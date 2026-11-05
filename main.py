"""
main.py - FastAPI Backend per ChatGPT Custom Action (Garmin Connect AI Running Coach)

Fornisce API REST protette per:
1. /activities/recent: Elenco sintetico delle ultime corse (passo, km, HR, Training Effect).
2. /activities/{activity_id}/details: Dettaglio avanzato con split km per km, zone cardio Z1-Z5 e dinamiche di corsa.
3. /health/summary: Stato di forma recente (RHR, Body Battery, Sonno, Tempo di recupero).
"""

import os
import sys

# Assicura encoding UTF-8 per console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from contextlib import asynccontextmanager
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Header, Path, Query, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

# Carica configurazione da file .env
load_dotenv()

from garmin_client import (
    ActivityDetailResponse,
    HealthSummaryResponse,
    PerformanceMetricsResponse,
    RecentActivityItem,
    garmin_service,
)
from setup_web import setup_router
from mcp.server.transport_security import TransportSecuritySettings
from mcp_server import mcp_server

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()

# Schemi di sicurezza per OpenAPI / Swagger UI / ChatGPT Action
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


# ==============================================================================
# Dipendenza di Autenticazione (Supporta sia X-API-Key che Bearer Token)
# ==============================================================================

async def verify_api_key(
    header_api_key: Optional[str] = Security(api_key_header_scheme),
    bearer_auth: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> str:
    """
    Verifica l'autenticazione tramite chiave API statica.
    Accetta sia l'header 'X-API-Key: <token>' sia 'Authorization: Bearer <token>',
    garantendo massima compatibilità con la configurazione Custom Action di ChatGPT.
    """
    token_candidate = None
    if header_api_key:
        token_candidate = header_api_key.strip()
    elif bearer_auth and bearer_auth.credentials:
        token_candidate = bearer_auth.credentials.strip()

    if not API_KEY:
        # Se la chiave non è configurata in .env, logghiamo un warning e blocchiamo l'accesso
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API_KEY non configurata nel file .env del server.",
        )

    if not token_candidate or token_candidate != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Non autorizzato: chiave API assente o non valida.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token_candidate


# ==============================================================================
# Lifespan: Inizializzazione Sessione Garmin all'avvio
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Esegue il bootstrap della sessione Garmin e del server MCP all'avvio."""
    print("\n[STARTUP] Avvio del Backend Garmin AI Coach...")
    try:
        success = garmin_service.initialize()
        if success:
            print("[OK] Client Garmin Connect inizializzato con successo!")
        else:
            print("[INFO] Sessione Garmin non ancora inizializzata. Apri /setup per effettuare il login.")
    except Exception as e:
        print(f"[WARN] Inizializzazione Garmin non completata: {e}")

    # Avvia il session manager MCP per gestire connessioni Streamable HTTP e SSE
    async with mcp_server.session_manager.run():
        yield
    print("[SHUTDOWN] Arresto del server in corso...")


# ==============================================================================
# Inizializzazione Applicazione FastAPI
# ==============================================================================

app = FastAPI(
    title="Garmin Connect AI Running Coach API",
    description=(
        "Backend REST ottimizzato specificamente per ChatGPT Custom Actions e Plugin MCP. "
        "Consente a un coach virtuale di analizzare gli allenamenti di corsa, split km, "
        "distribuzione nelle zone cardio (Z1-Z5), dinamiche di corsa, Body Battery, "
        "qualità del sonno e tempi di recupero direttamente dal profilo Garmin dell'atleta."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configurazione CORS (utile se richiamato da web browser)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Router di Setup Web per login Garmin interattivo con MFA
app.include_router(setup_router)

# Configurazione e montaggio del Server MCP per ChatGPT Plugin
mcp_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
mcp_http_subapp = mcp_server.streamable_http_app(streamable_http_path="/", transport_security=mcp_security)
mcp_sse_subapp = mcp_server.sse_app(sse_path="/", message_path="/messages/", transport_security=mcp_security)

# Esposizione sia di /mcp (Streamable HTTP) sia di /sse (Server-Sent Events)
app.mount("/mcp", mcp_http_subapp)
app.mount("/sse", mcp_sse_subapp)


# ==============================================================================
# Endpoint Pubblici di Diagnostica e Privacy
# ==============================================================================

@app.get("/", tags=["Health"], operation_id="getRootStatus")
async def root():
    """Endpoint informativo di stato."""
    is_ready = garmin_service.garmin is not None and garmin_service.garmin.client.is_authenticated
    return {
        "status": "online",
        "service": "Garmin Connect AI Coach Backend",
        "garmin_authenticated": is_ready,
        "mcp_streamable_url": f"{SERVER_URL}/mcp",
        "mcp_sse_url": f"{SERVER_URL}/sse",
        "docs": "/docs",
        "openapi_schema": "/openapi.json",
    }


@app.get("/health", tags=["Health"], operation_id="checkHealth")
async def health_check():
    """Health check per monitoraggio uptime su Railway / Render / Docker."""
    return {"status": "ok"}


@app.get("/privacy", response_class=HTMLResponse, tags=["Health"], operation_id="getPrivacyPolicy")
async def privacy_policy():
    """
    Informativa sulla privacy richiesta da OpenAI per le Custom Action di ChatGPT.
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <title>Privacy Policy - Garmin AI Coach Action</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.6; max-width: 750px; margin: 40px auto; padding: 0 20px; color: #1e293b; background: #f8fafc; }
            .card { background: white; padding: 32px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); border: 1px solid #e2e8f0; }
            h1 { color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 12px; }
            h2 { color: #0284c7; margin-top: 24px; }
            p, li { font-size: 15px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Informativa sulla Privacy - Garmin AI Coach Action</h1>
            <p>Questa applicazione connette direttamente ChatGPT ai dati Garmin Connect del titolare del token per scopi esclusivi di coaching atletico personale.</p>
            <h2>1. Raccolta e Utilizzo Dati</h2>
            <p>L'API estrae solo metriche sportive (distanze, tempi, battiti cardiaci, sonno e recupero) richieste esplicitamente dall'utente durante le sessioni di conversazione con ChatGPT.</p>
            <h2>2. Condivisione dei Dati con Terzi</h2>
            <p>I dati non vengono ceduti, venduti o condivisi con terze parti né memorizzati in database esterni persistenti al di fuori della sessione locale del client Garmin.</p>
            <h2>3. Sicurezza</h2>
            <p>Tutte le comunicazioni avvengono tramite crittografia HTTPS e sono protette da una chiave API statica privata impostata dall'utente.</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ==============================================================================
# Endpoint Protetto 1: Attività Recenti
# ==============================================================================

@app.get(
    "/activities/recent",
    response_model=List[RecentActivityItem],
    summary="Recupera la lista degli allenamenti recenti",
    operation_id="getRecentActivities",
    tags=["Garmin Activities"],
    dependencies=[Depends(verify_api_key)],
)
async def get_recent_activities(
    limit: int = Query(
        5,
        ge=1,
        le=15,
        description="Numero massimo di allenamenti recenti da recuperare (default: 5, max: 15)",
    ),
    activity_type: Optional[str] = Query(
        "running",
        description="Tipo di attività sportiva (default: 'running', oppure 'cycling', 'walking', o 'all')",
    ),
):
    """
    Restituisce un riepilogo sintetico e ottimizzato per token LLM degli ultimi allenamenti registrati.
    Include distanza, durata, passo medio, frequenza cardiaca media/massima,
    dislivello e gli effetti dell'allenamento (Aerobic e Anaerobic Training Effect).
    """
    try:
        activities = garmin_service.get_recent_activities(limit=limit, activity_type=activity_type)
        return activities
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Errore durante il recupero delle attività da Garmin: {str(e)}",
        )


# ==============================================================================
# Endpoint Protetto 2: Dettagli Approfonditi Corsa Singola
# ==============================================================================

@app.get(
    "/activities/{activity_id}/details",
    response_model=ActivityDetailResponse,
    summary="Recupera i dettagli approfonditi di una specifica corsa",
    operation_id="getActivityDetails",
    tags=["Garmin Activities"],
    dependencies=[Depends(verify_api_key)],
)
async def get_activity_details(
    activity_id: int = Path(
        ...,
        description="ID numerico dell'attività Garmin di cui analizzare la prestazione dettagliata",
    ),
):
    """
    Restituisce l'analisi approfondita di una singola corsa:
    - Split chilometrici lap per lap (passo al km, HR medio del km, dislivello +/-)
    - Tempo trascorso nelle 5 zone di frequenza cardiaca (Z1-Z5) con relative percentuali
    - Dinamiche di corsa (lunghezza del passo, oscillazione verticale, tempo di contatto al suolo)
    """
    try:
        details = garmin_service.get_activity_details(activity_id=activity_id)
        return details
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Errore durante il recupero dei dettagli dell'attività {activity_id}: {str(e)}",
        )


# ==============================================================================
# Endpoint Protetto 3: Stato di Salute e Recupero
# ==============================================================================

@app.get(
    "/health/summary",
    response_model=HealthSummaryResponse,
    summary="Recupera lo stato di salute, recupero e sonno",
    operation_id="getHealthSummary",
    tags=["Garmin Health"],
    dependencies=[Depends(verify_api_key)],
)
async def get_health_summary(
    target_date: Optional[str] = Query(
        None,
        description="Data nel formato ISO YYYY-MM-DD. Se omessa, recupera i parametri odierni e l'ultima notte di sonno.",
    ),
):
    """
    Restituisce lo stato fisiologico e la prontezza dell'atleta:
    - Frequenza cardiaca a riposo (RHR)
    - Body Battery (livello attuale, minimo, massimo, carica/scarica)
    - Metriche dell'ultima notte di sonno (durata totale, punteggio qualità, ore di sonno profondo/leggero/REM)
    - Tempo di recupero consigliato residue in ore e punteggio Training Readiness
    """
    try:
        summary = garmin_service.get_health_summary(target_date=target_date)
        return summary
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Errore durante il recupero dello stato di salute da Garmin: {str(e)}",
        )


# ==============================================================================
# Endpoint Protetto 4: Metriche di Prestazione, VO2 Max e Previsioni di Gara
# ==============================================================================

@app.get(
    "/performance/metrics",
    response_model=PerformanceMetricsResponse,
    summary="Recupera VO2 Max, Età di Fitness, Previsioni di Gara e Carico di Allenamento",
    operation_id="getPerformanceMetrics",
    tags=["Garmin Performance"],
    dependencies=[Depends(verify_api_key)],
)
async def get_performance_metrics(
    target_date: Optional[str] = Query(
        None,
        description="Data nel formato ISO YYYY-MM-DD. Se omessa, recupera le metriche più recenti.",
    ),
):
    """
    Restituisce le metriche avanzate per il coaching di corsa:
    - VO2 Max (stima attuale e categoria di efficienza aerobica)
    - Età di Fitness (Fitness Age vs Età anagrafica)
    - Previsioni dei tempi di gara Garmin: 5K, 10K, Mezza Maratona, Maratona con ritmi al km
    - Stato dell'allenamento (Training Status, Carico Acuto, ACWR, trend di forma)
    - Stato e variabilità cardiaca (HRV status e media 7 giorni)
    """
    try:
        metrics = garmin_service.get_performance_metrics(target_date=target_date)
        return metrics
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Errore durante il recupero delle metriche di prestazione da Garmin: {str(e)}",
        )


# ==============================================================================
# Personalizzazione Schema OpenAPI per ChatGPT Custom Action
# ==============================================================================

def custom_openapi():
    """
    Genera lo schema OpenAPI arricchito con:
    - Server URL pubblico
    - Schemi di sicurezza API Key e Bearer Token
    - Descrizioni sintetiche per il ragionamento LLM
    """
    current_url = os.getenv("SERVER_URL", SERVER_URL).rstrip("/")
    if app.openapi_schema and app.openapi_schema.get("servers", [{}])[0].get("url") == current_url:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Garmin Connect AI Running Coach API",
        version="1.0.0",
        description=(
            "API per il collegamento a ChatGPT Custom GPT Coach. "
            "Fornisce accesso in sola lettura ai dati di corsa, zone cardio, recupero e sonno Garmin."
        ),
        routes=app.routes,
    )

    # Imposta l'URL del server per le chiamate esterne di ChatGPT
    current_url = os.getenv("SERVER_URL", SERVER_URL).rstrip("/")
    openapi_schema["servers"] = [
        {
            "url": current_url,
            "description": "Garmin AI Coach Production Server",
        }
    ]

    # Configura la definizione di sicurezza esplicita per lo schema
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    openapi_schema["components"]["securitySchemes"] = {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Chiave API statica segreta configurata nella Custom Action di ChatGPT",
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "APIKey",
            "description": "Autenticazione alternativa tramite Bearer Token",
        },
    }

    # Applica i requisiti di sicurezza a livello globale nello schema
    openapi_schema["security"] = [
        {"ApiKeyAuth": []},
        {"BearerAuth": []},
    ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
