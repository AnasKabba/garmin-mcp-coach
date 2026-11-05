"""
garmin_client.py - Wrapper per Garmin Connect con gestione automatica della sessione,
ri-autenticazione trasparente ed estrazione/pulizia dei dati di corsa e salute.
"""

import base64
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from pydantic import BaseModel, Field

logger = logging.getLogger("garmin_client")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


# ==============================================================================
# Helper per Formattazione Metriche
# ==============================================================================

def format_seconds_to_duration(seconds: Optional[float]) -> str:
    """Formatta i secondi in formato MM:SS oppure HH:MM:SS."""
    if seconds is None or seconds <= 0:
        return "00:00"
    total_sec = int(round(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def speed_to_pace_str(speed_mps: Optional[float]) -> str:
    """
    Converte la velocità da m/s a passo di corsa (minuti:secondi al km).
    Ad esempio: 3.33 m/s -> 300 sec/km -> "05:00 min/km"
    """
    if speed_mps is None or speed_mps <= 0.1:
        return "N/A"
    seconds_per_km = 1000.0 / speed_mps
    if seconds_per_km > 3600:  # più lento di 1 ora a km
        return "N/A"
    minutes = int(seconds_per_km // 60)
    seconds = int(seconds_per_km % 60)
    return f"{minutes:02d}:{seconds:02d} min/km"


def format_cadence(cadence: Optional[float]) -> Optional[int]:
    """
    Normalizza la cadenza in passi al minuto (SPM - Steps Per Minute).
    Alcuni vecchi payload Garmin riportano le falcate singole (es. 85 rpm).
    Se il valore è inferiore a 110, lo raddoppia per ottenere i passi totali (es. 170 spm).
    """
    if cadence is None or cadence <= 0:
        return None
    c = int(round(cadence))
    if 40 <= c <= 110:
        c *= 2
    return c


# ==============================================================================
# Modelli Pydantic per Risposte Ottimizzate per ChatGPT Action
# ==============================================================================

class RecentActivityItem(BaseModel):
    activity_id: int = Field(..., description="Identificativo univoco dell'attività Garmin")
    name: str = Field(..., description="Nome o titolo dell'allenamento")
    start_time: str = Field(..., description="Data e ora di inizio locale (formato ISO)")
    activity_type: str = Field(..., description="Tipologia attività (es. running, trail_running)")
    distance_km: float = Field(..., description="Distanza totale percorsa in chilometri")
    duration_seconds: float = Field(..., description="Durata totale attiva in secondi")
    duration_formatted: str = Field(..., description="Durata formattata (es. '48:15' o '1:15:20')")
    avg_pace_min_per_km: str = Field(..., description="Passo medio in minuti e secondi al km (es. '04:55 min/km')")
    avg_speed_kmh: float = Field(..., description="Velocità media in km/h")
    elevation_gain_m: float = Field(..., description="Dislivello positivo totale in metri (+)")
    elevation_loss_m: float = Field(..., description="Dislivello negativo totale in metri (-)")
    avg_hr: Optional[int] = Field(None, description="Frequenza cardiaca media in bpm")
    max_hr: Optional[int] = Field(None, description="Frequenza cardiaca massima in bpm")
    aerobic_training_effect: Optional[float] = Field(None, description="Aerobic Training Effect da 0.0 a 5.0")
    aerobic_training_effect_message: Optional[str] = Field(None, description="Descrizione Garmin dell'effetto aerobico")
    anaerobic_training_effect: Optional[float] = Field(None, description="Anaerobic Training Effect da 0.0 a 5.0")
    avg_cadence_spm: Optional[int] = Field(None, description="Cadenza media in passi al minuto (SPM)")
    calories: Optional[int] = Field(None, description="Calorie stimate bruciate")


class KmSplit(BaseModel):
    split_number: int = Field(..., description="Numero progressivo del km / lap")
    distance_km: float = Field(..., description="Distanza del singolo split (solitamente 1.0 km)")
    duration_seconds: float = Field(..., description="Tempo impiegato per completare lo split")
    duration_formatted: str = Field(..., description="Tempo formattato dello split (MM:SS)")
    pace_min_per_km: str = Field(..., description="Passo medio dello split (es. '04:45 min/km')")
    avg_hr: Optional[int] = Field(None, description="Frequenza cardiaca media nel km")
    max_hr: Optional[int] = Field(None, description="Frequenza cardiaca massima nel km")
    elevation_gain_m: Optional[float] = Field(None, description="Metri di ascesa nel km")
    elevation_loss_m: Optional[float] = Field(None, description="Metri di discesa nel km")
    avg_cadence_spm: Optional[int] = Field(None, description="Cadenza media durante lo split")


class HeartRateZoneItem(BaseModel):
    zone_number: int = Field(..., description="Numero della zona cardio (1-5)")
    zone_name: str = Field(..., description="Etichetta fisiologica (es. Z2 - Fondo Lento Aerobico)")
    min_hr: int = Field(..., description="Limite inferiore bpm della zona")
    max_hr: Optional[int] = Field(None, description="Limite superiore bpm della zona")
    duration_seconds: float = Field(..., description="Secondi trascorsi nella zona")
    duration_formatted: str = Field(..., description="Tempo formattato trascorso nella zona (MM:SS)")
    percentage_of_total: float = Field(..., description="Percentuale del tempo totale della corsa in questa zona")


class RunningDynamics(BaseModel):
    avg_stride_length_m: Optional[float] = Field(None, description="Lunghezza media del passo in metri (es. 1.15 m)")
    avg_vertical_oscillation_cm: Optional[float] = Field(None, description="Oscillazione verticale media del busto in cm")
    avg_ground_contact_time_ms: Optional[float] = Field(None, description="Tempo medio di contatto al suolo in millisecondi (GCT)")
    avg_vertical_ratio_percent: Optional[float] = Field(None, description="Rapporto verticale percentuale (efficienza della corsa)")
    avg_ground_contact_balance: Optional[str] = Field(None, description="Bilanciamento contatto suolo SX / DX")


class ActivityDetailResponse(BaseModel):
    activity_id: int = Field(..., description="ID attività Garmin")
    name: str = Field(..., description="Nome dell'attività")
    start_time: str = Field(..., description="Data e ora di inizio")
    activity_type: str = Field(..., description="Tipo attività")
    distance_km: float = Field(..., description="Distanza totale km")
    duration_formatted: str = Field(..., description="Durata totale corsa")
    avg_pace_min_per_km: str = Field(..., description="Passo medio complessivo")
    avg_hr: Optional[int] = Field(None, description="Frequenza cardiaca media")
    max_hr: Optional[int] = Field(None, description="Frequenza cardiaca massima")
    elevation_gain_m: float = Field(..., description="Dislivello positivo totale")
    elevation_loss_m: float = Field(..., description="Dislivello negativo totale")
    aerobic_training_effect: Optional[float] = Field(None, description="Aerobic Training Effect (0.0-5.0)")
    anaerobic_training_effect: Optional[float] = Field(None, description="Anaerobic Training Effect (0.0-5.0)")
    splits: List[KmSplit] = Field(default_factory=list, description="Dettaglio split chilometrici (lap pace, lap HR, dislivello)")
    hr_zones: List[HeartRateZoneItem] = Field(default_factory=list, description="Tempo trascorso nelle zone cardio Z1-Z5")
    running_dynamics: Optional[RunningDynamics] = Field(None, description="Dinamiche di corsa avanzate se rilevate dal dispositivo")


class BodyBatterySummary(BaseModel):
    current: Optional[int] = Field(None, description="Livello Body Battery attuale o più recente (0-100)")
    min: Optional[int] = Field(None, description="Livello minimo registrato nella giornata")
    max: Optional[int] = Field(None, description="Livello massimo registrato nella giornata")
    charged: Optional[int] = Field(None, description="Punti Body Battery ricaricati (riposo/sonno)")
    drained: Optional[int] = Field(None, description="Punti Body Battery consumati (stress/attività)")


class SleepStageSummary(BaseModel):
    deep_hours: Optional[float] = Field(None, description="Ore di sonno profondo")
    light_hours: Optional[float] = Field(None, description="Ore di sonno leggero")
    rem_hours: Optional[float] = Field(None, description="Ore di sonno REM")
    awake_hours: Optional[float] = Field(None, description="Ore di veglia notturna")


class SleepSummary(BaseModel):
    date: str = Field(..., description="Data di riferimento della sessione di sonno")
    total_sleep_hours: Optional[float] = Field(None, description="Ore totali di sonno")
    total_sleep_formatted: Optional[str] = Field(None, description="Durata del sonno formattata (es. '7h 45m')")
    sleep_score: Optional[int] = Field(None, description="Punteggio qualità del sonno Garmin (0-100)")
    quality_qualifier: Optional[str] = Field(None, description="Valutazione qualitativa (es. EXCELLENT, GOOD, FAIR, POOR)")
    stages: Optional[SleepStageSummary] = Field(None, description="Ripartizione delle fasi del sonno")


class RecoverySummary(BaseModel):
    recovery_time_hours: Optional[float] = Field(None, description="Ore di recupero consigliate residue")
    recovery_time_formatted: Optional[str] = Field(None, description="Tempo di recupero formattato (es. '14h 30m')")
    training_readiness_score: Optional[int] = Field(None, description="Punteggio Training Readiness (0-100) se supportato")
    readiness_level: Optional[str] = Field(None, description="Livello prontezza all'allenamento (es. HIGH, MODERATE, LOW)")


class VO2MaxData(BaseModel):
    running_vo2max: Optional[float] = Field(None, description="VO2 Max corsa (ml/kg/min)")
    running_vo2max_precise: Optional[float] = Field(None, description="VO2 Max corsa valore preciso con decimali")
    cycling_vo2max: Optional[float] = Field(None, description="VO2 Max ciclismo se presente")
    category: Optional[str] = Field(None, description="Categoria o livello aerobico (es. Superiore, Eccellente)")
    date: Optional[str] = Field(None, description="Data dell'ultima stima VO2 Max")


class FitnessAgeData(BaseModel):
    fitness_age: Optional[float] = Field(None, description="Età di fitness stimata da Garmin in anni")
    chronological_age: Optional[int] = Field(None, description="Età anagrafica dell'atleta")
    age_difference: Optional[float] = Field(None, description="Anni guadagnati (età anagrafica - età di fitness)")
    vigorous_days_avg: Optional[float] = Field(None, description="Giorni medi di attività intensa a settimana")


class RacePredictionItem(BaseModel):
    distance: str = Field(..., description="Distanza di gara (es. 5K, 10K, Mezza Maratona, Maratona)")
    time_seconds: int = Field(..., description="Tempo stimato in secondi")
    time_formatted: str = Field(..., description="Tempo stimato formattato (es. '22:22' o '1:45:43')")
    avg_pace: str = Field(..., description="Passo medio di gara stimato al km (es. '04:28 min/km')")


class HRVData(BaseModel):
    status: Optional[str] = Field(None, description="Stato HRV (es. BALANCED, UNBALANCED, LOW)")
    weekly_avg: Optional[int] = Field(None, description="Media HRV degli ultimi 7 giorni in ms")
    last_night_avg: Optional[int] = Field(None, description="Media HRV dell'ultima notte in ms")
    last_night_5min_high: Optional[int] = Field(None, description="Picco massimo 5 minuti nell'ultima notte in ms")
    baseline_low: Optional[int] = Field(None, description="Limite inferiore intervallo di equilibrio in ms")
    baseline_high: Optional[int] = Field(None, description="Limite superiore intervallo di equilibrio in ms")
    feedback_phrase: Optional[str] = Field(None, description="Valutazione qualitativa Garmin sull'equilibrio HRV")


class TrainingStatusData(BaseModel):
    training_status: Optional[str] = Field(None, description="Stato allenamento (es. RECOVERY, PRODUCTIVE, MAINTAINING, PEAKING, OVERREACHING)")
    feedback_phrase: Optional[str] = Field(None, description="Frase di stato del carico (es. RECOVERY_2)")
    acute_load: Optional[float] = Field(None, description="Carico acuto attuale (ultimi 7 giorni)")
    chronic_load_min: Optional[float] = Field(None, description="Limite minimo del range ottimale di carico cronico")
    chronic_load_max: Optional[float] = Field(None, description="Limite massimo del range ottimale di carico cronico")
    acwr_status: Optional[str] = Field(None, description="Stato del rapporto acuto/cronico (es. OPTIMAL, LOW, VERY_HIGH)")
    fitness_trend: Optional[str] = Field(None, description="Trend della forma fisica")


class DailyBiometricsData(BaseModel):
    steps: Optional[int] = Field(None, description="Passi totali percorsi nella giornata")
    distance_km: Optional[float] = Field(None, description="Distanza totale a piedi / corsa in km")
    active_calories: Optional[int] = Field(None, description="Calorie attive bruciate (kcal)")
    bmr_calories: Optional[int] = Field(None, description="Calorie basali a riposo (kcal)")
    total_calories: Optional[int] = Field(None, description="Calorie totali (kcal)")
    moderate_intensity_minutes: Optional[int] = Field(None, description="Minuti di intensità moderata")
    vigorous_intensity_minutes: Optional[int] = Field(None, description="Minuti di intensità vigorosa")
    floors_ascended: Optional[float] = Field(None, description="Piani di scale saliti")
    resting_hr: Optional[int] = Field(None, description="Frequenza cardiaca a riposo (RHR) in bpm")
    min_hr: Optional[int] = Field(None, description="Frequenza cardiaca minima della giornata")
    max_hr: Optional[int] = Field(None, description="Frequenza cardiaca massima della giornata")
    avg_stress_level: Optional[int] = Field(None, description="Livello di stress medio della giornata (0-100)")
    max_stress_level: Optional[int] = Field(None, description="Livello di stress massimo della giornata")
    avg_waking_respiration: Optional[float] = Field(None, description="Frequenza respiratoria media a riposo (respiri/min)")


class PerformanceMetricsResponse(BaseModel):
    query_date: str = Field(..., description="Data della rilevazione")
    vo2_max: Optional[VO2MaxData] = Field(None, description="VO2 Max e stato aerobico")
    fitness_age: Optional[FitnessAgeData] = Field(None, description="Età di fitness e confronto con età anagrafica")
    race_predictions: List[RacePredictionItem] = Field(default_factory=list, description="Previsioni tempi e ritmi di gara")
    training_status: Optional[TrainingStatusData] = Field(None, description="Stato allenamento e carico acuto/cronico")
    hrv: Optional[HRVData] = Field(None, description="Stato HRV e recupero fisiologico")


class HealthSummaryResponse(BaseModel):
    query_date: str = Field(..., description="Data analizzata (YYYY-MM-DD)")
    resting_heart_rate_bpm: Optional[int] = Field(None, description="Frequenza cardiaca a riposo (RHR) in bpm")
    body_battery: Optional[BodyBatterySummary] = Field(None, description="Stato Body Battery")
    sleep: Optional[SleepSummary] = Field(None, description="Dati dell'ultima sessione di sonno")
    recovery: Optional[RecoverySummary] = Field(None, description="Tempo di recupero consigliato e prontezza")
    daily_biometrics: Optional[DailyBiometricsData] = Field(None, description="Attività giornaliera: passi, calorie, intensità, stress")
    hrv: Optional[HRVData] = Field(None, description="Variabilità della frequenza cardiaca (HRV)")
    vo2_max: Optional[VO2MaxData] = Field(None, description="Stima VO2 Max più recente")
    fitness_age: Optional[FitnessAgeData] = Field(None, description="Età di fitness stimata")
    training_status: Optional[TrainingStatusData] = Field(None, description="Stato allenamento e carico acuto")


# ==============================================================================
# GarminClient Wrapper con Auto-Reconnect e Parsing
# ==============================================================================

ZONE_NAMES = {
    1: "Z1 - Recupero Attivo / Warm Up",
    2: "Z2 - Fondo Lento / Aerobica Base",
    3: "Z3 - Fondo Medio / Tempo Run",
    4: "Z4 - Soglia Anaerobica / Threshold",
    5: "Z5 - Massimale / VO2 Max",
}


class GarminClient:
    def __init__(self):
        self.garmin: Optional[Garmin] = None
        self.tokens_dir = os.getenv("GARMIN_TOKENS_DIR", "./tokens").strip()
        self.tokens_base64 = os.getenv("GARMIN_TOKENS_BASE64", "").strip()
        self.email = os.getenv("GARMIN_EMAIL", "").strip()
        self.password = os.getenv("GARMIN_PASSWORD", "").strip()
        self._is_ready = False

    def initialize(self) -> bool:
        """
        Carica i token e inizializza la sessione Garmin Connect.
        Priorità:
        1. Stringa GARMIN_TOKENS_BASE64 (se fornita nelle variabili d'ambiente).
        2. File token su disco in GARMIN_TOKENS_DIR (es. ./tokens/garmin_tokens.json).
        3. Email e password se presenti (senza MFA, non consigliato per la produzione).
        """
        logger.info("Inizializzazione client Garmin Connect...")
        self.garmin = Garmin()

        # 1. Tentativo con Base64 da variabile d'ambiente
        if self.tokens_base64:
            try:
                logger.info("Caricamento token da variabile GARMIN_TOKENS_BASE64...")
                decoded_json = base64.b64decode(self.tokens_base64).decode("utf-8")
                self.garmin.client.loads(decoded_json)
                self.garmin._load_profile_and_settings()
                self._is_ready = True
                logger.info(f"✅ Connesso a Garmin Connect con successo! Utente: {self.garmin.get_full_name()}")
                return True
            except Exception as e:
                logger.warning(f"Caricamento token da GARMIN_TOKENS_BASE64 fallito: {e}. Procedo con fallback.")

        # 2. Tentativo con file su disco
        tokens_path = Path(self.tokens_dir)
        token_file = tokens_path / "garmin_tokens.json" if tokens_path.is_dir() else tokens_path

        if token_file.exists() or tokens_path.exists():
            try:
                logger.info(f"Caricamento token dal percorso su disco: {tokens_path}...")
                self.garmin.login(str(tokens_path))
                self._is_ready = True
                logger.info(f"✅ Connesso a Garmin Connect tramite file token! Utente: {self.garmin.get_full_name()}")
                return True
            except Exception as e:
                logger.warning(f"Caricamento token da disco fallito: {e}. Procedo con fallback.")

        # 3. Fallback con credenziali dirette se presenti
        if self.email and self.password:
            try:
                logger.info(f"Tentativo login con credenziali per {self.email}...")
                self.garmin = Garmin(email=self.email, password=self.password)
                self.garmin.login()
                # Salva i token per i futuri avvii
                try:
                    tokens_path.mkdir(parents=True, exist_ok=True)
                    self.garmin.client.dump(str(tokens_path))
                    logger.info(f"Token salvati su disco in: {tokens_path}")
                except Exception as dump_err:
                    logger.debug(f"Impossibile salvare i token su disco: {dump_err}")
                self._is_ready = True
                logger.info(f"✅ Connesso con credenziali! Utente: {self.garmin.get_full_name()}")
                return True
            except Exception as e:
                logger.error(f"Login con credenziali fallito: {e}")

        logger.error(
            "❌ Nessun metodo di autenticazione valido trovato! "
            "Esegui prima lo script: python setup_auth.py"
        )
        self._is_ready = False
        return False

    def _ensure_authenticated(self):
        """Verifica lo stato della sessione e tenta la riconnessione se necessario."""
        if not self._is_ready or not self.garmin or not self.garmin.client.is_authenticated:
            success = self.initialize()
            if not success:
                raise GarminConnectAuthenticationError(
                    "Sessione Garmin non autenticata. Esegui 'python setup_auth.py' per rinnovare i token."
                )

    def execute_with_retry(self, func, *args, **kwargs):
        """Esegue una chiamata API Garmin con ri-autenticazione trasparente in caso di 401/token scaduto."""
        self._ensure_authenticated()
        try:
            return func(*args, **kwargs)
        except (GarminConnectAuthenticationError, Exception) as e:
            err_str = str(e).lower()
            if "401" in err_str or "unauthorized" in err_str or "authentication" in err_str or isinstance(e, GarminConnectAuthenticationError):
                logger.warning("Token Garmin scaduto o rifiutato. Tentativo di ri-autenticazione automatica...")
                try:
                    # Prova refresh interno della sessione
                    if hasattr(self.garmin.client, "_refresh_session"):
                        self.garmin.client._refresh_session()
                        return func(*args, **kwargs)
                except Exception:
                    pass

                # Se il refresh fallisce, tenta re-inizializzazione completa
                if self.initialize():
                    return func(*args, **kwargs)
            raise

    # --------------------------------------------------------------------------
    # 1. Attività Recenti
    # --------------------------------------------------------------------------
    def get_recent_activities(self, limit: int = 5, activity_type: Optional[str] = "running") -> List[RecentActivityItem]:
        """
        Recupera le attività recenti filtrate per tipologia e genera un payload snello per LLM.
        """
        # Se viene richiesto un filtro per tipo, recuperiamo più elementi per compensare eventuali attività di altro tipo
        fetch_limit = limit if not activity_type else min(limit * 3, 40)

        raw_activities = self.execute_with_retry(self.garmin.get_activities, 0, fetch_limit)
        if not raw_activities:
            return []

        results: List[RecentActivityItem] = []
        for act in raw_activities:
            # Controllo tipologia
            type_obj = act.get("activityType", {})
            type_key = type_obj.get("typeKey", "").lower() if isinstance(type_obj, dict) else ""

            if activity_type and activity_type.lower() != "all":
                if activity_type.lower() not in type_key:
                    continue

            dist_m = act.get("distance", 0.0) or 0.0
            dist_km = round(dist_m / 1000.0, 2)
            dur_s = act.get("duration", 0.0) or act.get("movingDuration", 0.0) or 0.0
            avg_speed_mps = act.get("averageSpeed", 0.0) or 0.0
            avg_speed_kmh = round(avg_speed_mps * 3.6, 2)
            pace_str = speed_to_pace_str(avg_speed_mps)

            # Cadenza
            cadence_raw = act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageCadence")
            cadence_spm = format_cadence(cadence_raw)

            item = RecentActivityItem(
                activity_id=int(act.get("activityId")),
                name=act.get("activityName") or "Allenamento Corsa",
                start_time=act.get("startTimeLocal") or act.get("startTimeGMT") or "",
                activity_type=type_key or "running",
                distance_km=dist_km,
                duration_seconds=dur_s,
                duration_formatted=format_seconds_to_duration(dur_s),
                avg_pace_min_per_km=pace_str,
                avg_speed_kmh=avg_speed_kmh,
                elevation_gain_m=round(act.get("elevationGain", 0.0) or 0.0, 1),
                elevation_loss_m=round(act.get("elevationLoss", 0.0) or 0.0, 1),
                avg_hr=int(act["averageHR"]) if act.get("averageHR") else None,
                max_hr=int(act["maxHR"]) if act.get("maxHR") else None,
                aerobic_training_effect=act.get("aerobicTrainingEffect"),
                aerobic_training_effect_message=act.get("aerobicTrainingEffectMessage"),
                anaerobic_training_effect=act.get("anaerobicTrainingEffect"),
                avg_cadence_spm=cadence_spm,
                calories=int(act["calories"]) if act.get("calories") else None,
            )
            results.append(item)
            if len(results) >= limit:
                break

        return results

    # --------------------------------------------------------------------------
    # 2. Dettagli Attività Singola (Splits, HR Zones, Dinamiche di Corsa)
    # --------------------------------------------------------------------------
    def get_activity_details(self, activity_id: int) -> ActivityDetailResponse:
        """
        Estrae i dati approfonditi di una specifica attività:
        - Split chilometrici (passo, HR, dislivello)
        - Tempo trascorso in ciascuna zona cardio (Z1-Z5)
        - Dinamiche di corsa (stride, oscillazione verticale, tempo contatto suolo)
        """
        # 1. Recupero sommario dell'attività
        act_summary = self.execute_with_retry(self.garmin.get_activity, activity_id)
        if not act_summary:
            raise ValueError(f"Attività {activity_id} non trovata.")

        # In Garmin l'oggetto può trovarsi sotto summaryDTO o direttamente a livello radice
        s_dto = act_summary.get("summaryDTO") or act_summary
        m_dto = act_summary.get("metadataDTO") or {}

        act_name = act_summary.get("activityName") or s_dto.get("activityName") or "Dettaglio Corsa"
        start_time = s_dto.get("startTimeLocal") or s_dto.get("startTimeGMT") or ""
        type_key = s_dto.get("activityType", {}).get("typeKey", "running") if isinstance(s_dto.get("activityType"), dict) else "running"

        dist_m = s_dto.get("distance", 0.0) or 0.0
        dist_km = round(dist_m / 1000.0, 2)
        dur_s = s_dto.get("duration", 0.0) or s_dto.get("movingDuration", 0.0) or 0.0
        dur_formatted = format_seconds_to_duration(dur_s)
        avg_speed_mps = s_dto.get("averageSpeed", 0.0) or 0.0
        pace_str = speed_to_pace_str(avg_speed_mps)

        # 2. Recupero Splits Chilometrici
        splits_list: List[KmSplit] = []
        try:
            splits_data = self.execute_with_retry(self.garmin.get_activity_splits, activity_id)
            # Garmin espone splitSummaries (split ogni km) oppure lapDTOs (laps manuali/automatici)
            raw_splits = splits_data.get("splitSummaries") or splits_data.get("lapDTOs") or []

            for idx, lap in enumerate(raw_splits, start=1):
                lap_dist_m = lap.get("distance", 0.0) or 0.0
                lap_dist_km = round(lap_dist_m / 1000.0, 2)
                lap_dur_s = lap.get("duration", 0.0) or lap.get("movingDuration", 0.0) or 0.0
                lap_speed = lap.get("averageSpeed", 0.0) or 0.0

                split_num = lap.get("no") or lap.get("lapIndex") or idx
                cad_raw = lap.get("averageRunCadence") or lap.get("averageCadence")

                splits_list.append(
                    KmSplit(
                        split_number=int(split_num),
                        distance_km=lap_dist_km,
                        duration_seconds=round(lap_dur_s, 1),
                        duration_formatted=format_seconds_to_duration(lap_dur_s),
                        pace_min_per_km=speed_to_pace_str(lap_speed),
                        avg_hr=int(lap["averageHR"]) if lap.get("averageHR") else None,
                        max_hr=int(lap["maxHR"]) if lap.get("maxHR") else None,
                        elevation_gain_m=round(lap.get("elevationGain", 0.0) or 0.0, 1),
                        elevation_loss_m=round(lap.get("elevationLoss", 0.0) or 0.0, 1),
                        avg_cadence_spm=format_cadence(cad_raw),
                    )
                )
        except Exception as e:
            logger.warning(f"Impossibile recuperare splits per {activity_id}: {e}")

        # 3. Recupero Zone Cardio (Z1-Z5)
        hr_zones_list: List[HeartRateZoneItem] = []
        try:
            zones_data = self.execute_with_retry(self.garmin.get_activity_hr_in_timezones, activity_id)
            if zones_data and isinstance(zones_data, list):
                total_zone_secs = sum(float(z.get("secsInZone", 0.0) or 0.0) for z in zones_data)
                for z in zones_data:
                    z_num = int(z.get("zoneNumber", 0))
                    secs = float(z.get("secsInZone", 0.0) or 0.0)
                    pct = round((secs / total_zone_secs * 100.0), 1) if total_zone_secs > 0 else 0.0

                    hr_zones_list.append(
                        HeartRateZoneItem(
                            zone_number=z_num,
                            zone_name=ZONE_NAMES.get(z_num, f"Zona {z_num}"),
                            min_hr=int(z.get("zoneLowBoundary", 0)),
                            max_hr=int(z["zoneHigherBoundary"]) if z.get("zoneHigherBoundary") else None,
                            duration_seconds=round(secs, 1),
                            duration_formatted=format_seconds_to_duration(secs),
                            percentage_of_total=pct,
                        )
                    )
        except Exception as e:
            logger.warning(f"Impossibile recuperare zone HR per {activity_id}: {e}")

        # 4. Dinamiche di Corsa
        running_dyn: Optional[RunningDynamics] = None
        stride_len = s_dto.get("avgStrideLength") or s_dto.get("averageStrideLength")
        vert_osc = s_dto.get("avgVerticalOscillation")
        gct = s_dto.get("avgGroundContactTime")
        vert_ratio = s_dto.get("avgVerticalRatio")
        gct_balance = s_dto.get("avgGroundContactBalance") or s_dto.get("avgGroundContactBalanceLeft")

        if any(v is not None for v in (stride_len, vert_osc, gct, vert_ratio)):
            # Se stride_len è espresso in centimetri (es. 115.0), converti in metri (1.15)
            stride_m = None
            if stride_len is not None:
                stride_m = round(stride_len / 100.0, 2) if stride_len > 20 else round(stride_len, 2)

            # Vertical oscillation: Garmin a volte riporta mm o cm
            vert_cm = None
            if vert_osc is not None:
                vert_cm = round(vert_osc / 10.0, 1) if vert_osc > 30 else round(vert_osc, 1)

            running_dyn = RunningDynamics(
                avg_stride_length_m=stride_m,
                avg_vertical_oscillation_cm=vert_cm,
                avg_ground_contact_time_ms=round(gct, 1) if gct is not None else None,
                avg_vertical_ratio_percent=round(vert_ratio, 1) if vert_ratio is not None else None,
                avg_ground_contact_balance=str(gct_balance) if gct_balance is not None else None,
            )

        return ActivityDetailResponse(
            activity_id=activity_id,
            name=act_name,
            start_time=start_time,
            activity_type=type_key,
            distance_km=dist_km,
            duration_formatted=dur_formatted,
            avg_pace_min_per_km=pace_str,
            avg_hr=int(s_dto["averageHR"]) if s_dto.get("averageHR") else None,
            max_hr=int(s_dto["maxHR"]) if s_dto.get("maxHR") else None,
            elevation_gain_m=round(s_dto.get("elevationGain", 0.0) or 0.0, 1),
            elevation_loss_m=round(s_dto.get("elevationLoss", 0.0) or 0.0, 1),
            aerobic_training_effect=s_dto.get("aerobicTrainingEffect"),
            anaerobic_training_effect=s_dto.get("anaerobicTrainingEffect"),
            splits=splits_list,
            hr_zones=hr_zones_list,
            running_dynamics=running_dyn,
        )

    # --------------------------------------------------------------------------
    # 3. Stato di Forma e Salute (Resting HR, Body Battery, Sonno, Recupero)
    # --------------------------------------------------------------------------
    def get_health_summary(self, target_date: Optional[str] = None) -> HealthSummaryResponse:
        """
        Recupera la panoramica di salute recente:
        - Frequenza cardiaca a riposo (RHR)
        - Body Battery (min, max, attuale, caricato, scaricato)
        - Dettagli ultimo sonno (durata, punteggio, fasi)
        - Tempo di recupero consigliato e Training Readiness
        """
        if not target_date:
            target_date = date.today().isoformat()

        # 1. Frequenza cardiaca a riposo (Resting HR)
        resting_hr = None
        try:
            hr_data = self.execute_with_retry(self.garmin.get_heart_rates, target_date)
            if hr_data and isinstance(hr_data, dict):
                resting_hr = hr_data.get("restingHeartRate")

            if not resting_hr:
                stats = self.execute_with_retry(self.garmin.get_stats, target_date)
                if stats and isinstance(stats, dict):
                    resting_hr = stats.get("restingHeartRate") or stats.get("minHeartRate")
        except Exception as e:
            logger.warning(f"Impossibile recuperare Resting HR per {target_date}: {e}")

        # 2. Body Battery
        body_battery_summary: Optional[BodyBatterySummary] = None
        try:
            bb_data = self.execute_with_retry(self.garmin.get_body_battery, target_date)
            # bb_data è tipicamente una lista di record giornalieri
            if bb_data and isinstance(bb_data, list) and len(bb_data) > 0:
                day_bb = bb_data[0]
                values_array = day_bb.get("bodyBatteryValuesArray", [])
                curr_val = None
                min_val = None
                max_val = None

                if values_array:
                    # Ciascun elemento è [timestamp, battery_level, ...]
                    levels = [pt[1] for pt in values_array if isinstance(pt, (list, tuple)) and len(pt) >= 2 and pt[1] is not None]
                    if levels:
                        curr_val = levels[-1]
                        min_val = min(levels)
                        max_val = max(levels)

                if curr_val is None:
                    curr_val = day_bb.get("endBodyBatteryLevel") or day_bb.get("startBodyBatteryLevel")

                body_battery_summary = BodyBatterySummary(
                    current=curr_val,
                    min=min_val,
                    max=max_val,
                    charged=day_bb.get("charged"),
                    drained=day_bb.get("drained"),
                )
        except Exception as e:
            logger.warning(f"Impossibile recuperare Body Battery per {target_date}: {e}")

        # 3. Sonno (Verifica oggi e ieri nel caso la sincronizzazione sia in ritardo)
        sleep_summary: Optional[SleepSummary] = None
        for check_date in [target_date, (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()]:
            try:
                raw_sleep = self.execute_with_retry(self.garmin.get_sleep_data, check_date)
                if raw_sleep and isinstance(raw_sleep, dict):
                    daily_sleep = raw_sleep.get("dailySleepDTO", {})
                    sleep_sec = daily_sleep.get("sleepTimeSeconds", 0) or 0
                    if sleep_sec > 0:
                        total_hours = round(sleep_sec / 3600.0, 1)
                        h = int(sleep_sec // 3600)
                        m = int((sleep_sec % 3600) // 60)
                        sleep_fmt = f"{h}h {m:02d}m"

                        # Punteggio sonno
                        sleep_scores = daily_sleep.get("sleepScores", {})
                        overall_score = sleep_scores.get("overall", {}).get("value")
                        qualifier = sleep_scores.get("overall", {}).get("qualifierKey")

                        # Fasi del sonno
                        deep_s = daily_sleep.get("deepSleepSeconds", 0) or 0
                        light_s = daily_sleep.get("lightSleepSeconds", 0) or 0
                        rem_s = daily_sleep.get("remSleepSeconds", 0) or 0
                        awake_s = daily_sleep.get("awakeSleepSeconds", 0) or 0

                        stages = SleepStageSummary(
                            deep_hours=round(deep_s / 3600.0, 1),
                            light_hours=round(light_s / 3600.0, 1),
                            rem_hours=round(rem_s / 3600.0, 1),
                            awake_hours=round(awake_s / 3600.0, 1),
                        )

                        sleep_summary = SleepSummary(
                            date=check_date,
                            total_sleep_hours=total_hours,
                            total_sleep_formatted=sleep_fmt,
                            sleep_score=overall_score,
                            quality_qualifier=qualifier,
                            stages=stages,
                        )
                        break  # Trovata sessione di sonno valida
            except Exception as e:
                logger.warning(f"Impossibile recuperare dati sonno per {check_date}: {e}")

        # 4. Tempo di Recupero e Training Readiness
        recovery_summary: Optional[RecoverySummary] = None
        try:
            readiness_data = self.execute_with_retry(self.garmin.get_training_readiness, target_date)
            # In Garmin readiness_data può essere un dict o lista
            readiness_item = readiness_data[0] if isinstance(readiness_data, list) and readiness_data else readiness_data
            if isinstance(readiness_item, dict):
                recov_min = readiness_item.get("recoveryTime")  # Attenzione: recoveryTime in minuti!
                recov_hours = None
                recov_fmt = None
                if recov_min is not None:
                    recov_hours = round(recov_min / 60.0, 1)
                    r_h = int(recov_min // 60)
                    r_m = int(recov_min % 60)
                    recov_fmt = f"{r_h}h {r_m:02d}m" if recov_min > 0 else "0h (Completamente recuperato)"

                score = readiness_item.get("score")
                level = readiness_item.get("level")

                recovery_summary = RecoverySummary(
                    recovery_time_hours=recov_hours,
                    recovery_time_formatted=recov_fmt,
                    training_readiness_score=score,
                    readiness_level=level,
                )
        except Exception:
            # Fallback a training_status se get_training_readiness non è disponibile
            try:
                status_data = self.execute_with_retry(self.garmin.get_training_status, target_date)
                if status_data and isinstance(status_data, dict):
                    recov_hours = status_data.get("recoveryTime")
                    if recov_hours is not None:
                        recovery_summary = RecoverySummary(
                            recovery_time_hours=float(recov_hours),
                            recovery_time_formatted=f"{int(recov_hours)}h",
                            training_readiness_score=None,
                            readiness_level=status_data.get("trainingStatus"),
                        )
            except Exception as e:
                logger.debug(f"Nessun dato di recupero/readiness trovato per {target_date}: {e}")

        # 5. Attività giornaliera (Passi, Calorie, Intensità, Stress, Respirazione)
        daily_biometrics: Optional[DailyBiometricsData] = None
        try:
            user_summary = self.execute_with_retry(self.garmin.get_user_summary, target_date)
            if user_summary and isinstance(user_summary, dict):
                dist_m = user_summary.get("totalDistanceMeters")
                daily_biometrics = DailyBiometricsData(
                    steps=user_summary.get("totalSteps"),
                    distance_km=round(dist_m / 1000.0, 2) if dist_m else None,
                    active_calories=int(user_summary.get("activeKilocalories")) if user_summary.get("activeKilocalories") is not None else None,
                    bmr_calories=int(user_summary.get("bmrKilocalories")) if user_summary.get("bmrKilocalories") is not None else None,
                    total_calories=int(user_summary.get("totalKilocalories")) if user_summary.get("totalKilocalories") is not None else None,
                    moderate_intensity_minutes=user_summary.get("moderateIntensityMinutes"),
                    vigorous_intensity_minutes=user_summary.get("vigorousIntensityMinutes"),
                    floors_ascended=round(user_summary.get("floorsAscended", 0), 1) if user_summary.get("floorsAscended") is not None else None,
                    resting_hr=user_summary.get("restingHeartRate") or resting_hr,
                    min_hr=user_summary.get("minHeartRate"),
                    max_hr=user_summary.get("maxHeartRate"),
                    avg_stress_level=user_summary.get("averageStressLevel"),
                    max_stress_level=user_summary.get("maxStressLevel"),
                    avg_waking_respiration=user_summary.get("avgWakingRespirationValue"),
                )
                if not resting_hr:
                    resting_hr = user_summary.get("restingHeartRate")
        except Exception as e:
            logger.warning(f"Impossibile recuperare Daily Biometrics per {target_date}: {e}")

        # 6. Variabilità cardiaca (HRV)
        hrv_summary: Optional[HRVData] = None
        try:
            raw_hrv = self.execute_with_retry(self.garmin.get_hrv_data, target_date)
            if raw_hrv and isinstance(raw_hrv, dict):
                hrv_s = raw_hrv.get("hrvSummary", {})
                baseline = hrv_s.get("baseline", {})
                hrv_summary = HRVData(
                    status=hrv_s.get("status"),
                    weekly_avg=hrv_s.get("weeklyAvg"),
                    last_night_avg=hrv_s.get("lastNightAvg"),
                    last_night_5min_high=hrv_s.get("lastNight5MinHigh"),
                    baseline_low=baseline.get("lowUpper") or baseline.get("balancedLow"),
                    baseline_high=baseline.get("balancedUpper"),
                    feedback_phrase=hrv_s.get("feedbackPhrase"),
                )
        except Exception as e:
            logger.warning(f"Impossibile recuperare HRV per {target_date}: {e}")

        # 7. VO2 Max più recente
        vo2_max_summary: Optional[VO2MaxData] = None
        try:
            raw_max = self.execute_with_retry(self.garmin.get_max_metrics_latest)
            if raw_max and isinstance(raw_max, dict):
                gen = raw_max.get("generic", {})
                if gen and gen.get("vo2MaxValue"):
                    vo2_val = gen.get("vo2MaxValue")
                    cat = "Superiore" if vo2_val >= 52 else ("Eccellente" if vo2_val >= 47 else "Buono")
                    vo2_max_summary = VO2MaxData(
                        running_vo2max=vo2_val,
                        running_vo2max_precise=gen.get("vo2MaxPreciseValue"),
                        category=cat,
                        date=gen.get("calendarDate"),
                    )
        except Exception as e:
            logger.warning(f"Impossibile recuperare VO2 Max: {e}")

        # 8. Fitness Age
        fitness_age_summary: Optional[FitnessAgeData] = None
        try:
            fa_raw = self.execute_with_retry(self.garmin.get_fitness_age_details, target_date)
            if fa_raw and isinstance(fa_raw, dict):
                fit_age = fa_raw.get("fitnessAge")
                chr_age = fa_raw.get("chronologicalAge")
                diff = round(chr_age - fit_age, 1) if fit_age and chr_age else None
                vig = fa_raw.get("components", {}).get("vigorousDaysAvg", {}).get("value")
                fitness_age_summary = FitnessAgeData(
                    fitness_age=round(fit_age, 1) if fit_age else None,
                    chronological_age=chr_age,
                    age_difference=diff,
                    vigorous_days_avg=vig,
                )
        except Exception as e:
            logger.warning(f"Impossibile recuperare Fitness Age: {e}")

        # 9. Training Status & Carico Acuto
        training_status_summary: Optional[TrainingStatusData] = None
        try:
            ts_raw = self.execute_with_retry(self.garmin.get_daily_training_status, target_date)
            if ts_raw and isinstance(ts_raw, dict):
                l_data = ts_raw.get("latestTrainingStatusData", {})
                for _, val in l_data.items():
                    phrase = val.get("trainingStatusFeedbackPhrase")
                    acute = val.get("acuteTrainingLoadDTO", {})
                    training_status_summary = TrainingStatusData(
                        training_status=val.get("sport"),
                        feedback_phrase=phrase,
                        acute_load=acute.get("dailyTrainingLoadAcute"),
                        chronic_load_min=acute.get("minTrainingLoadChronic"),
                        chronic_load_max=acute.get("maxTrainingLoadChronic"),
                        acwr_status=acute.get("acwrStatus"),
                        fitness_trend=str(val.get("fitnessTrend")),
                    )
                    break
        except Exception as e:
            logger.warning(f"Impossibile recuperare Training Status: {e}")

        return HealthSummaryResponse(
            query_date=target_date,
            resting_heart_rate_bpm=resting_hr,
            body_battery=body_battery_summary,
            sleep=sleep_summary,
            recovery=recovery_summary,
            daily_biometrics=daily_biometrics,
            hrv=hrv_summary,
            vo2_max=vo2_max_summary,
            fitness_age=fitness_age_summary,
            training_status=training_status_summary,
        )

    def get_performance_metrics(self, target_date: Optional[str] = None) -> PerformanceMetricsResponse:
        """
        Recupera le metriche di prestazione atletica avanzate:
        VO2 Max, Età di Fitness, Previsioni di gara (5K, 10K, Mezza Maratona, Maratona),
        Stato dell'allenamento, Carico acuto/cronico e HRV.
        """
        self._ensure_authenticated()
        if not target_date:
            target_date = date.today().isoformat()

        # 1. VO2 Max
        vo2_data: Optional[VO2MaxData] = None
        try:
            raw_max = self.execute_with_retry(self.garmin.get_max_metrics_latest)
            if raw_max and isinstance(raw_max, dict):
                gen = raw_max.get("generic", {})
                if gen and gen.get("vo2MaxValue"):
                    vo2_val = gen.get("vo2MaxValue")
                    cat = "Superiore" if vo2_val >= 52 else ("Eccellente" if vo2_val >= 47 else "Buono")
                    vo2_data = VO2MaxData(
                        running_vo2max=vo2_val,
                        running_vo2max_precise=gen.get("vo2MaxPreciseValue"),
                        category=cat,
                        date=gen.get("calendarDate"),
                    )
        except Exception as e:
            logger.warning(f"Errore recupero VO2 Max in performance: {e}")

        # 2. Fitness Age
        fitness_age_data: Optional[FitnessAgeData] = None
        try:
            fa_raw = self.execute_with_retry(self.garmin.get_fitness_age_details, target_date)
            if fa_raw and isinstance(fa_raw, dict):
                fit_age = fa_raw.get("fitnessAge")
                chr_age = fa_raw.get("chronologicalAge")
                diff = round(chr_age - fit_age, 1) if fit_age and chr_age else None
                vig = fa_raw.get("components", {}).get("vigorousDaysAvg", {}).get("value")
                fitness_age_data = FitnessAgeData(
                    fitness_age=round(fit_age, 1) if fit_age else None,
                    chronological_age=chr_age,
                    age_difference=diff,
                    vigorous_days_avg=vig,
                )
        except Exception as e:
            logger.warning(f"Errore recupero Fitness Age: {e}")

        # 3. Race Predictions
        race_predictions: List[RacePredictionItem] = []
        try:
            rp = self.execute_with_retry(self.garmin.get_race_predictions)
            if rp and isinstance(rp, dict):
                dist_defs = [
                    ("5K", rp.get("time5K"), 5.0),
                    ("10K", rp.get("time10K"), 10.0),
                    ("Mezza Maratona (21.1 km)", rp.get("timeHalfMarathon"), 21.0975),
                    ("Maratona (42.2 km)", rp.get("timeMarathon"), 42.195),
                ]
                for d_name, d_sec, d_km in dist_defs:
                    if d_sec:
                        time_fmt = format_seconds_to_duration(d_sec)
                        p_sec = d_sec / d_km
                        p_m = int(p_sec // 60)
                        p_s = int(p_sec % 60)
                        pace_str = f"{p_m:02d}:{p_s:02d} min/km"
                        race_predictions.append(
                            RacePredictionItem(
                                distance=d_name,
                                time_seconds=int(d_sec),
                                time_formatted=time_fmt,
                                avg_pace=pace_str,
                            )
                        )
        except Exception as e:
            logger.warning(f"Errore recupero Race Predictions: {e}")

        # 4. Training Status & Carico
        t_status_data: Optional[TrainingStatusData] = None
        try:
            ts_raw = self.execute_with_retry(self.garmin.get_daily_training_status, target_date)
            if ts_raw and isinstance(ts_raw, dict):
                l_data = ts_raw.get("latestTrainingStatusData", {})
                for _, val in l_data.items():
                    phrase = val.get("trainingStatusFeedbackPhrase")
                    acute = val.get("acuteTrainingLoadDTO", {})
                    t_status_data = TrainingStatusData(
                        training_status=val.get("sport"),
                        feedback_phrase=phrase,
                        acute_load=acute.get("dailyTrainingLoadAcute"),
                        chronic_load_min=acute.get("minTrainingLoadChronic"),
                        chronic_load_max=acute.get("maxTrainingLoadChronic"),
                        acwr_status=acute.get("acwrStatus"),
                        fitness_trend=str(val.get("fitnessTrend")),
                    )
                    break
        except Exception as e:
            logger.warning(f"Errore recupero Training Status: {e}")

        # 5. HRV
        hrv_res: Optional[HRVData] = None
        try:
            raw_hrv = self.execute_with_retry(self.garmin.get_hrv_data, target_date)
            if raw_hrv and isinstance(raw_hrv, dict):
                hrv_s = raw_hrv.get("hrvSummary", {})
                baseline = hrv_s.get("baseline", {})
                hrv_res = HRVData(
                    status=hrv_s.get("status"),
                    weekly_avg=hrv_s.get("weeklyAvg"),
                    last_night_avg=hrv_s.get("lastNightAvg"),
                    last_night_5min_high=hrv_s.get("lastNight5MinHigh"),
                    baseline_low=baseline.get("lowUpper") or baseline.get("balancedLow"),
                    baseline_high=baseline.get("balancedUpper"),
                    feedback_phrase=hrv_s.get("feedbackPhrase"),
                )
        except Exception as e:
            logger.warning(f"Errore recupero HRV: {e}")

        return PerformanceMetricsResponse(
            query_date=target_date,
            vo2_max=vo2_data,
            fitness_age=fitness_age_data,
            race_predictions=race_predictions,
            training_status=t_status_data,
            hrv=hrv_res,
        )


# Istanza singleton globale pronta per essere condivisa con FastAPI
garmin_service = GarminClient()
