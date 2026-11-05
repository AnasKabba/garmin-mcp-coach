"""
test_endpoints.py - Test automatici di validazione per sicurezza ed endpoint FastAPI
"""

import os
import unittest
from unittest.mock import MagicMock, patch

# Imposta una chiave fittizia per il test prima di importare main
os.environ["API_KEY"] = "test_secret_key_12345"
os.environ["SERVER_URL"] = "https://api.testgarmin.com"

from fastapi.testclient import TestClient
from main import app
from garmin_client import (
    ActivityDetailResponse,
    HealthSummaryResponse,
    HeartRateZoneItem,
    KmSplit,
    RecentActivityItem,
    RunningDynamics,
    garmin_service,
)

client = TestClient(app)


class TestGarminBackend(unittest.TestCase):

    def test_root_and_health(self):
        """Verifica che gli endpoint pubblici rispondano correttamente."""
        r_root = client.get("/")
        self.assertEqual(r_root.status_code, 200)
        self.assertIn("status", r_root.json())

        r_health = client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json(), {"status": "ok"})

        r_privacy = client.get("/privacy")
        self.assertEqual(r_privacy.status_code, 200)
        self.assertIn("Informativa sulla Privacy", r_privacy.text)

    def test_auth_protection(self):
        """Verifica che gli endpoint siano bloccati senza API Key o con chiave errata."""
        # Nessun header
        res_no_auth = client.get("/activities/recent")
        self.assertEqual(res_no_auth.status_code, 401)

        # Chiave errata in X-API-Key
        res_wrong_key = client.get("/activities/recent", headers={"X-API-Key": "wrong_key"})
        self.assertEqual(res_wrong_key.status_code, 401)

        # Chiave errata in Bearer
        res_wrong_bearer = client.get("/activities/recent", headers={"Authorization": "Bearer wrong_bearer"})
        self.assertEqual(res_wrong_bearer.status_code, 401)

    def test_get_recent_activities_success(self):
        """Verifica il recupero delle corse recenti con autenticazione valida."""
        mock_items = [
            RecentActivityItem(
                activity_id=12345678,
                name="Corsa Mattutina 10km",
                start_time="2026-11-04T07:30:00",
                activity_type="running",
                distance_km=10.05,
                duration_seconds=3015.0,
                duration_formatted="50:15",
                avg_pace_min_per_km="05:00 min/km",
                avg_speed_kmh=12.0,
                elevation_gain_m=45.0,
                elevation_loss_m=42.0,
                avg_hr=152,
                max_hr=168,
                aerobic_training_effect=3.4,
                aerobic_training_effect_message="Miglioramento capacità aerobica",
                anaerobic_training_effect=0.5,
                avg_cadence_spm=172,
                calories=680,
            )
        ]

        with patch.object(garmin_service, "get_recent_activities", return_value=mock_items):
            # Test con header X-API-Key
            res_header = client.get("/activities/recent?limit=1", headers={"X-API-Key": "test_secret_key_12345"})
            self.assertEqual(res_header.status_code, 200)
            data = res_header.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["activity_id"], 12345678)
            self.assertEqual(data[0]["avg_pace_min_per_km"], "05:00 min/km")

            # Test con Bearer Token
            res_bearer = client.get("/activities/recent?limit=1", headers={"Authorization": "Bearer test_secret_key_12345"})
            self.assertEqual(res_bearer.status_code, 200)

    def test_get_activity_details_success(self):
        """Verifica il dettaglio approfondito (splits, zone cardio, dinamiche di corsa)."""
        mock_detail = ActivityDetailResponse(
            activity_id=98765432,
            name="Corsa con Ripetute",
            start_time="2026-11-03T18:00:00",
            activity_type="running",
            distance_km=8.0,
            duration_formatted="38:20",
            avg_pace_min_per_km="04:47 min/km",
            avg_hr=160,
            max_hr=182,
            elevation_gain_m=35.0,
            elevation_loss_m=35.0,
            aerobic_training_effect=4.1,
            anaerobic_training_effect=2.3,
            splits=[
                KmSplit(
                    split_number=1,
                    distance_km=1.0,
                    duration_seconds=300.0,
                    duration_formatted="05:00",
                    pace_min_per_km="05:00 min/km",
                    avg_hr=145,
                    max_hr=152,
                    elevation_gain_m=5.0,
                    elevation_loss_m=2.0,
                    avg_cadence_spm=168,
                ),
                KmSplit(
                    split_number=2,
                    distance_km=1.0,
                    duration_seconds=270.0,
                    duration_formatted="04:30",
                    pace_min_per_km="04:30 min/km",
                    avg_hr=170,
                    max_hr=178,
                    elevation_gain_m=3.0,
                    elevation_loss_m=1.0,
                    avg_cadence_spm=176,
                ),
            ],
            hr_zones=[
                HeartRateZoneItem(
                    zone_number=2,
                    zone_name="Z2 - Fondo Lento",
                    min_hr=125,
                    max_hr=142,
                    duration_seconds=600.0,
                    duration_formatted="10:00",
                    percentage_of_total=26.1,
                ),
                HeartRateZoneItem(
                    zone_number=4,
                    zone_name="Z4 - Soglia Anaerobica",
                    min_hr=160,
                    max_hr=175,
                    duration_seconds=1200.0,
                    duration_formatted="20:00",
                    percentage_of_total=52.2,
                ),
            ],
            running_dynamics=RunningDynamics(
                avg_stride_length_m=1.18,
                avg_vertical_oscillation_cm=7.9,
                avg_ground_contact_time_ms=235.0,
                avg_vertical_ratio_percent=6.7,
                avg_ground_contact_balance="50.1% L / 49.9% R",
            ),
        )

        with patch.object(garmin_service, "get_activity_details", return_value=mock_detail):
            res = client.get("/activities/98765432/details", headers={"X-API-Key": "test_secret_key_12345"})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["activity_id"], 98765432)
            self.assertEqual(len(data["splits"]), 2)
            self.assertEqual(len(data["hr_zones"]), 2)
            self.assertIsNotNone(data["running_dynamics"])
            self.assertEqual(data["running_dynamics"]["avg_stride_length_m"], 1.18)

    def test_get_health_summary_success(self):
        """Verifica il sommario di salute (Body Battery, Sonno, Recupero)."""
        mock_health = HealthSummaryResponse(
            query_date="2026-11-05",
            resting_heart_rate_bpm=48,
            body_battery={
                "current": 72,
                "min": 25,
                "max": 88,
                "charged": 63,
                "drained": 50,
            },
            sleep={
                "date": "2026-11-05",
                "total_sleep_hours": 7.8,
                "total_sleep_formatted": "7h 48m",
                "sleep_score": 85,
                "quality_qualifier": "EXCELLENT",
                "stages": {
                    "deep_hours": 1.9,
                    "light_hours": 4.1,
                    "rem_hours": 1.8,
                    "awake_hours": 0.3,
                },
            },
            recovery={
                "recovery_time_hours": 12.0,
                "recovery_time_formatted": "12h 00m",
                "training_readiness_score": 75,
                "readiness_level": "MODERATE",
            },
        )

        with patch.object(garmin_service, "get_health_summary", return_value=mock_health):
            res = client.get("/health/summary", headers={"X-API-Key": "test_secret_key_12345"})
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["resting_heart_rate_bpm"], 48)
            self.assertEqual(data["body_battery"]["current"], 72)
            self.assertEqual(data["sleep"]["sleep_score"], 85)
            self.assertEqual(data["recovery"]["recovery_time_hours"], 12.0)

    def test_mcp_endpoints_registered(self):
        """Verifica che gli endpoint MCP siano registrati e attivi."""
        r_root = client.get("/")
        self.assertEqual(r_root.status_code, 200)
        data = r_root.json()
        self.assertIn("mcp_streamable_url", data)
        self.assertIn("mcp_sse_url", data)


if __name__ == "__main__":
    unittest.main()
