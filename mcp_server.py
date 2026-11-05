"""
mcp_server.py - Server MCP (Model Context Protocol) nativo per ChatGPT Plugins.
Espone le funzioni di estrazione dati Garmin come strumenti standard MCP.
"""

import json
from typing import Optional
from mcp.server.mcpserver import MCPServer
from garmin_client import garmin_service

# Inizializza l'istanza del server MCP
mcp_server = MCPServer(
    name="Garmin Running Coach",
    description="Plugin per analisi corse, split chilometrici, zone cardio, sonno e recupero da Garmin Connect.",
    version="1.0.0",
)


@mcp_server.tool(name="get_recent_activities")
def get_recent_activities(limit: int = 5, activity_type: str = "running") -> str:
    """
    Recupera le ultime attività sportive registrate su Garmin Connect.
    
    Parametri:
    - limit: numero di attività da recuperare (default: 5, max: 15)
    - activity_type: tipologia di attività (es. 'running', 'cycling', 'all')
    
    Restituisce un JSON con: id attività, nome, data/ora, km, durata, passo medio (min/km),
    dislivello (+/-), frequenza cardiaca media/massima, Training Effect e cadenza.
    """
    try:
        acts = garmin_service.get_recent_activities(limit=limit, activity_type=activity_type)
        return json.dumps([a.model_dump() for a in acts], ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Errore nel recupero attività: {str(e)}"})


@mcp_server.tool(name="get_activity_details")
def get_activity_details(activity_id: int) -> str:
    """
    Recupera i dettagli approfonditi di una specifica corsa registrata su Garmin.
    
    Parametri:
    - activity_id: identificativo numerico dell'attività Garmin
    
    Restituisce un JSON con:
    - Splits chilometrici lap per lap (passo al km, HR medio, dislivello +/- per ogni km)
    - Tempo trascorso nelle 5 zone cardio (Z1-Z5) con percentuali e tempi formattati
    - Dinamiche di corsa (cadenza, lunghezza passo, oscillazione verticale, tempo contatto suolo)
    """
    try:
        details = garmin_service.get_activity_details(activity_id=activity_id)
        return json.dumps(details.model_dump(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Errore nel recupero dettagli attività: {str(e)}"})


@mcp_server.tool(name="get_health_summary")
def get_health_summary(target_date: Optional[str] = None) -> str:
    """
    Recupera lo stato di salute, recupero fisiologico e prontezza dell'atleta.
    
    Parametri:
    - target_date: data nel formato YYYY-MM-DD (facoltativa, default: oggi)
    
    Restituisce un JSON completo con:
    - Frequenza cardiaca a riposo (RHR) e range min/max
    - Body Battery (livello attuale, minimo, massimo, punti ricaricati e consumati)
    - Sonno dell'ultima notte (durata totale, punteggio 0-100, ore profondo/REM/leggero/veglia)
    - Tempo di recupero consigliato in ore e punteggio Training Readiness
    - Biometria quotidiana: passi, km percorsi, calorie attive/basali, minuti di intensità, piani saliti, stress medio e respirazione
    - Stato HRV (settimanale, ultima notte, intervallo di equilibrio)
    - Stima più recente del VO2 Max e dell'Età di Fitness
    - Stato del carico di allenamento (Training Status, Carico Acuto, ACWR)
    """
    try:
        summary = garmin_service.get_health_summary(target_date=target_date if target_date else None)
        return json.dumps(summary.model_dump(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Errore nel recupero dati salute: {str(e)}"})


@mcp_server.tool(name="get_performance_metrics")
def get_performance_metrics(target_date: Optional[str] = None) -> str:
    """
    Recupera le metriche di prestazione atletica avanzate e stime competitive dell'atleta.
    
    Parametri:
    - target_date: data nel formato YYYY-MM-DD (facoltativa, default: oggi)
    
    Restituisce un JSON con:
    - VO2 Max (valore esatto, categoria aerobica es. 'Superiore/Eccellente')
    - Età di Fitness (Fitness Age stimata vs età anagrafica)
    - Previsioni tempi di gara ufficiali Garmin: 5K, 10K, Mezza Maratona (21.1 km), Maratona (42.2 km) con tempi stimati e ritmi al km calcolati (es. 04:28 min/km)
    - Stato dell'allenamento e Carico Acuto (Acute Training Load, ACWR, trend di fitness)
    - Stato di equilibrio della variabilità cardiaca (HRV)
    """
    try:
        perf = garmin_service.get_performance_metrics(target_date=target_date if target_date else None)
        return json.dumps(perf.model_dump(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Errore nel recupero metriche prestazione: {str(e)}"})
