"""
setup_web.py - Interfaccia Web Sicura e Mobile-First per il Login Garmin Connect con MFA.

Consente all'utente di aprire un link sicuro dal proprio smartphone (o browser),
inserire le credenziali Garmin e il codice MFA a 6 cifre senza esporli in chat o console.
I token generati vengono salvati su disco e nel file .env in formato Base64.
"""

import base64
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv, set_key
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

load_dotenv()

setup_router = APIRouter(tags=["Setup Web"])

# Memorizza le sessioni MFA in attesa (session_id -> {client, client_state, timestamp})
PENDING_MFA_SESSIONS: Dict[str, Dict[str, Any]] = {}


class AuthStartRequest(BaseModel):
    email: str = Field(..., description="Email dell'account Garmin Connect")
    password: str = Field(..., description="Password dell'account Garmin Connect")
    key: str = Field(..., description="Chiave API di sicurezza del backend")


class AuthCompleteRequest(BaseModel):
    session_id: str = Field(..., description="ID della sessione MFA generato nello step 1")
    mfa_code: str = Field(..., description="Codice di verifica a 6 cifre ricevuto via SMS/Email")
    client_state: Optional[Dict[str, Any]] = Field(None, description="Stato serializzato della sessione")
    key: str = Field(..., description="Chiave API di sicurezza del backend")


def get_current_api_key() -> str:
    return os.getenv("API_KEY", "").strip()


def persist_garmin_session(garmin_client: Garmin):
    """Salva i token su disco e aggiorna il file .env con la stringa Base64."""
    tokens_dir = os.getenv("GARMIN_TOKENS_DIR", "./tokens").strip()
    tokens_path = Path(tokens_dir)
    tokens_path.mkdir(parents=True, exist_ok=True)

    # 1. Salva i token su disco
    garmin_client.client.dump(str(tokens_path))

    # 2. Serializza in Base64 per ambienti Cloud
    token_json_str = garmin_client.client.dumps()
    token_base64 = base64.b64encode(token_json_str.encode("utf-8")).decode("utf-8")

    # 3. Aggiorna il file .env
    env_file = Path(".env")
    if env_file.exists():
        try:
            set_key(str(env_file), "GARMIN_TOKENS_BASE64", token_base64)
            set_key(str(env_file), "GARMIN_TOKENS_DIR", str(tokens_dir))
        except Exception:
            pass

    # 4. Aggiorna il singleton garmin_service se importabile
    try:
        from garmin_client import garmin_service
        garmin_service.garmin = garmin_client
        garmin_service._is_ready = True
    except Exception:
        pass


@setup_router.get("/setup", response_class=HTMLResponse)
async def setup_page(key: Optional[str] = Query(None, description="Chiave API per accedere alla pagina di setup")):
    """
    Pagina web responsive e sicura per il login da smartphone.
    """
    expected_key = get_current_api_key()
    if not expected_key or not key or key.strip() != expected_key:
        return HTMLResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content="""
            <!DOCTYPE html>
            <html lang="it">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Accesso Negato</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #0f172a; color: #f8fafc; }
                    .card { background: #1e293b; padding: 32px; border-radius: 16px; border: 1px solid #334155; max-width: 420px; text-align: center; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
                    h2 { color: #f43f5e; margin-top: 0; }
                    p { color: #94a3b8; font-size: 15px; line-height: 1.5; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h2>🚫 Accesso Non Autorizzato</h2>
                    <p>La chiave di sicurezza fornita nel link non è valida o è assente.</p>
                    <p>Assicurati di aprire il link completo fornito con il parametro <code>?key=...</code></p>
                </div>
            </body>
            </html>
            """
        )

    # HTML con interfaccia mobile-first ultra pulita
    html_template = f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Garmin AI Coach - Connessione Sicura</title>
        <style>
            * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background: #0f172a;
                color: #f8fafc;
                margin: 0;
                padding: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }}
            .card {{
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 20px;
                padding: 28px 24px;
                width: 100%;
                max-width: 440px;
                box-shadow: 0 20px 35px -10px rgba(0, 0, 0, 0.5);
            }}
            .header {{
                text-align: center;
                margin-bottom: 24px;
            }}
            .icon-badge {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 56px;
                height: 56px;
                background: rgba(14, 165, 233, 0.15);
                color: #38bdf8;
                border-radius: 16px;
                font-size: 28px;
                margin-bottom: 12px;
                border: 1px solid rgba(56, 189, 248, 0.3);
            }}
            h1 {{
                font-size: 22px;
                font-weight: 700;
                margin: 0 0 6px 0;
                color: #ffffff;
            }}
            p.sub {{
                color: #94a3b8;
                font-size: 14px;
                margin: 0;
                line-height: 1.4;
            }}
            .form-group {{
                margin-bottom: 18px;
                text-align: left;
            }}
            label {{
                display: block;
                font-size: 13px;
                font-weight: 600;
                color: #cbd5e1;
                margin-bottom: 6px;
            }}
            .input-wrapper {{
                position: relative;
            }}
            input {{
                width: 100%;
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 14px 16px;
                font-size: 16px;
                color: #ffffff;
                outline: none;
                transition: all 0.2s ease;
            }}
            input:focus {{
                border-color: #38bdf8;
                box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
            }}
            .btn {{
                width: 100%;
                background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);
                color: white;
                border: none;
                border-radius: 12px;
                padding: 15px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                margin-top: 10px;
                min-height: 50px;
            }}
            .btn:hover {{
                background: linear-gradient(135deg, #0369a1 0%, #075985 100%);
            }}
            .btn:disabled {{
                opacity: 0.6;
                cursor: not-allowed;
            }}
            .alert {{
                padding: 12px 14px;
                border-radius: 10px;
                font-size: 14px;
                line-height: 1.4;
                margin-bottom: 18px;
                display: none;
            }}
            .alert-error {{
                background: rgba(244, 63, 94, 0.15);
                border: 1px solid rgba(244, 63, 94, 0.3);
                color: #fda4af;
            }}
            .alert-info {{
                background: rgba(14, 165, 233, 0.15);
                border: 1px solid rgba(14, 165, 233, 0.3);
                color: #7dd3fc;
            }}
            .spinner {{
                border: 3px solid rgba(255, 255, 255, 0.3);
                border-top: 3px solid #ffffff;
                border-radius: 50%;
                width: 20px;
                height: 20px;
                animation: spin 0.8s linear infinite;
                display: none;
            }}
            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
            .mfa-box {{
                display: none;
                animation: fadeIn 0.3s ease;
            }}
            .success-box {{
                display: none;
                text-align: center;
                animation: fadeIn 0.3s ease;
            }}
            .mfa-input {{
                letter-spacing: 6px;
                font-size: 24px;
                font-weight: 700;
                text-align: center;
            }}
            .security-badge {{
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 6px;
                color: #64748b;
                font-size: 12px;
                margin-top: 20px;
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(6px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <!-- Step 1: Credenziali -->
            <div id="credentials-step">
                <div class="header">
                    <div class="icon-badge">🏃</div>
                    <h1>Connetti Garmin</h1>
                    <p class="sub">Accedi al tuo account Garmin Connect per abilitare il tuo AI Coach su ChatGPT.</p>
                </div>

                <div id="error-alert" class="alert alert-error"></div>

                <form id="auth-form" onsubmit="handleStartAuth(event)">
                    <div class="form-group">
                        <label for="email">Email Garmin Connect</label>
                        <input type="email" id="email" required placeholder="tua.email@esempio.com" autocomplete="email" autocapitalize="none">
                    </div>
                    <div class="form-group">
                        <label for="password">Password Garmin Connect</label>
                        <input type="password" id="password" required placeholder="••••••••••••" autocomplete="current-password">
                    </div>
                    <button type="submit" id="btn-submit" class="btn">
                        <span id="btn-text">Accedi in Modo Sicuro</span>
                        <div id="btn-spinner" class="spinner"></div>
                    </button>
                </form>
            </div>

            <!-- Step 2: MFA 2FA -->
            <div id="mfa-step" class="mfa-box">
                <div class="header">
                    <div class="icon-badge">🔐</div>
                    <h1>Codice di Verifica (MFA)</h1>
                    <p class="sub">Garmin ha inviato un codice a 6 cifre via Email o SMS. Inseriscilo qui sotto.</p>
                </div>

                <div id="mfa-error-alert" class="alert alert-error"></div>

                <form id="mfa-form" onsubmit="handleCompleteMfa(event)">
                    <div class="form-group">
                        <label for="mfa-code" style="text-align:center;">Codice a 6 Cifre</label>
                        <input type="text" id="mfa-code" class="mfa-input" required maxlength="6" pattern="[0-9]*" inputmode="numeric" placeholder="123456" autocomplete="one-time-code">
                    </div>
                    <button type="submit" id="btn-mfa-submit" class="btn">
                        <span id="btn-mfa-text">Verifica e Salva Token</span>
                        <div id="btn-mfa-spinner" class="spinner"></div>
                    </button>
                </form>
            </div>

            <!-- Step 3: Successo -->
            <div id="success-step" class="success-box">
                <div class="icon-badge" style="background: rgba(34, 197, 94, 0.15); color: #4ade80; border-color: rgba(34, 197, 94, 0.3);">✅</div>
                <h1 style="color: #4ade80;">Connesso con Successo!</h1>
                <p id="user-display" class="sub" style="font-weight: 600; color: #ffffff; margin-top: 6px;"></p>
                <div class="alert alert-info" style="display: block; margin-top: 18px; text-align: left;">
                    🔒 <strong>Token di sessione salvati sul server:</strong><br>
                    Il backend ora è sincronizzato. Non dovrai reinserire le credenziali ad ogni avvio.
                </div>
                <p class="sub" style="margin-top: 16px;">Puoi chiudere questa scheda del telefono e tornare alla conversazione con il tuo <strong>Coach AI</strong> su ChatGPT!</p>
            </div>

            <div class="security-badge">
                <span>🔒 Sviluppato da <strong>AK Dev.</strong> • Crittografia End-to-End</span>
            </div>
        </div>

        <script>
            const API_KEY = "{key}";
            let currentSessionId = null;
            let currentClientState = null;

            function showError(elementId, msg) {{
                const el = document.getElementById(elementId);
                el.innerText = msg;
                el.style.display = "block";
            }}

            function hideError(elementId) {{
                const el = document.getElementById(elementId);
                el.style.display = "none";
            }}

            async function handleStartAuth(e) {{
                e.preventDefault();
                hideError("error-alert");

                const email = document.getElementById("email").value.trim();
                const password = document.getElementById("password").value;
                const btn = document.getElementById("btn-submit");
                const btnText = document.getElementById("btn-text");
                const spinner = document.getElementById("btn-spinner");

                btn.disabled = true;
                btnText.innerText = "Connessione a Garmin in corso...";
                spinner.style.display = "inline-block";

                try {{
                    const res = await fetch("/api/auth/start", {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{ email, password, key: API_KEY }})
                    }});
                    const data = await res.json();

                    if (!res.ok) {{
                        throw new Error(data.detail || "Errore durante l'accesso a Garmin");
                    }}

                    if (data.status === "needs_mfa") {{
                        // Mostra il form MFA
                        currentSessionId = data.session_id;
                        currentClientState = data.client_state;
                        document.getElementById("credentials-step").style.display = "none";
                        document.getElementById("mfa-step").style.display = "block";
                        document.getElementById("mfa-code").focus();
                    }} else if (data.status === "success") {{
                        // Successo immediato senza MFA
                        showSuccess(data.user);
                    }}
                }} catch (err) {{
                    showError("error-alert", err.message);
                }} finally {{
                    btn.disabled = false;
                    btnText.innerText = "Accedi in Modo Sicuro";
                    spinner.style.display = "none";
                }}
            }}

            async function handleCompleteMfa(e) {{
                e.preventDefault();
                hideError("mfa-error-alert");

                const mfaCode = document.getElementById("mfa-code").value.trim();
                const btn = document.getElementById("btn-mfa-submit");
                const btnText = document.getElementById("btn-mfa-text");
                const spinner = document.getElementById("btn-mfa-spinner");

                btn.disabled = true;
                btnText.innerText = "Verifica del codice...";
                spinner.style.display = "inline-block";

                try {{
                    const res = await fetch("/api/auth/complete", {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{
                            session_id: currentSessionId,
                            mfa_code: mfaCode,
                            client_state: currentClientState,
                            key: API_KEY
                        }})
                    }});
                    const data = await res.json();

                    if (!res.ok) {{
                        throw new Error(data.detail || "Codice MFA non valido o scaduto");
                    }}

                    showSuccess(data.user);
                }} catch (err) {{
                    showError("mfa-error-alert", err.message);
                }} finally {{
                    btn.disabled = false;
                    btnText.innerText = "Verifica e Salva Token";
                    spinner.style.display = "none";
                }}
            }}

            function showSuccess(userName) {{
                document.getElementById("credentials-step").style.display = "none";
                document.getElementById("mfa-step").style.display = "none";
                document.getElementById("success-step").style.display = "block";
                if (userName) {{
                    document.getElementById("user-display").innerText = "Atleta: " + userName;
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_template)


@setup_router.post("/api/auth/start")
async def api_auth_start(req: AuthStartRequest):
    """
    Inizia l'autenticazione con email e password.
    Se l'account ha il 2FA (MFA) abilitato, restituisce needs_mfa e l'ID sessione.
    """
    expected_key = get_current_api_key()
    if not expected_key or req.key.strip() != expected_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chiave API non autorizzata.")

    try:
        # Inizializza client Garmin con return_on_mfa abilitato
        client = Garmin(
            email=req.email.strip(),
            password=req.password,
            return_on_mfa=True,
        )

        mfa_status, client_state = client.login()

        if mfa_status == "needs_mfa":
            session_id = secrets.token_hex(16)
            PENDING_MFA_SESSIONS[session_id] = {
                "client": client,
                "client_state": client_state,
                "created_at": time.time(),
            }
            return {
                "status": "needs_mfa",
                "session_id": session_id,
                "client_state": client_state,
            }

        # Login completato al primo colpo senza MFA
        persist_garmin_session(client)
        full_name = client.get_full_name() if hasattr(client, "get_full_name") else "Utente Garmin"
        return {"status": "success", "user": full_name}

    except GarminConnectTooManyRequestsError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Troppi tentativi di login consecutivi su Garmin Connect. Attendi qualche minuto: {e}",
        )
    except GarminConnectAuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Credenziali Garmin non corrette. Verifica email e password: {e}",
        )
    except GarminConnectConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Errore di connessione ai server Garmin: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore imprevisto durante l'accesso: {e}",
        )


@setup_router.post("/api/auth/complete")
async def api_auth_complete(req: AuthCompleteRequest):
    """
    Completa l'autenticazione inviando il codice MFA a 6 cifre.
    Salva i token e attiva la sessione per l'API.
    """
    expected_key = get_current_api_key()
    if not expected_key or req.key.strip() != expected_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Chiave API non autorizzata.")

    session_data = PENDING_MFA_SESSIONS.pop(req.session_id, None)

    client: Optional[Garmin] = None
    client_state: Optional[Dict[str, Any]] = None

    if session_data:
        client = session_data.get("client")
        client_state = session_data.get("client_state")

    if client is None:
        client = Garmin(return_on_mfa=True)
        client_state = req.client_state

    if not client_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sessione MFA scaduta o non trovata. Riprova effettuando nuovamente il login.",
        )

    try:
        # Completa il login con il codice di verifica
        client.resume_login(client_state, req.mfa_code.strip())

        # Salva i token su disco e aggiorna il .env
        persist_garmin_session(client)

        full_name = client.get_full_name() if hasattr(client, "get_full_name") else "Utente Garmin"
        return {"status": "success", "user": full_name}

    except GarminConnectAuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Codice MFA errato o scaduto. Riprova: {e}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore durante la verifica del codice MFA: {e}",
        )
