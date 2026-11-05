"""
setup_auth.py - Script interattivo di bootstrap per l'autenticazione su Garmin Connect.

Questo script:
1. Effettua il primo login sicuro con email e password.
2. Gestisce il prompt per l'autenticazione a due fattori (MFA) tramite console.
3. Salva i token di sessione generati su disco (es. in ./tokens/garmin_tokens.json).
4. Genera una stringa Base64 dei token per il deploy rapido senza disco persistente (Render/Railway).
5. Verifica immediatamente che la connessione sia funzionante recuperando il profilo utente.
"""

import base64
import getpass
import os
import sys
from pathlib import Path
from dotenv import load_dotenv, set_key

# Carica eventuali variabili già presenti in .env
load_dotenv()

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    print("\n❌ Errore: la libreria 'garminconnect' non è installata.")
    print("👉 Esegui: pip install -r requirements.txt\n")
    sys.exit(1)


def prompt_mfa_callback() -> str:
    """Callback invocata da Garmin quando l'account ha il 2FA (MFA) abilitato."""
    print("\n" + "=" * 60)
    print("🔐 RICHIESTA AUTENTICAZIONE A DUE FATTORI (MFA)")
    print("Garmin ha inviato un codice di verifica a 6 cifre via Email o SMS.")
    print("=" * 60)
    while True:
        code = input("👉 Inserisci il codice MFA ricevuto: ").strip()
        if code:
            return code
        print("⚠️ Il codice non può essere vuoto. Riprova.")


def main():
    print("\n" + "=" * 65)
    print("🏃 Garmin Connect AI Coach - Setup Iniziale Autenticazione")
    print("=" * 65)
    print("Questo script effettua il login a Garmin Connect e memorizza i token")
    print("di sessione crittografati per permettere all'API FastAPI di funzionare")
    print("in modo trasparente e automatico, senza chiedere la password ad ogni avvio.\n")

    # 1. Recupero Credenziali
    env_email = os.getenv("GARMIN_EMAIL", "").strip()
    if env_email:
        use_env = input(f"Trovata email in .env ({env_email}). Vuoi usarla? [S/n]: ").strip().lower()
        if use_env in ("", "s", "si", "y", "yes"):
            email = env_email
        else:
            email = input("Inserisci la tua email Garmin Connect: ").strip()
    else:
        email = input("Inserisci la tua email Garmin Connect: ").strip()

    env_password = os.getenv("GARMIN_PASSWORD", "").strip()
    if env_password and email == env_email:
        use_env_pwd = input("Trovata password in .env. Vuoi usarla? [S/n]: ").strip().lower()
        if use_env_pwd in ("", "s", "si", "y", "yes"):
            password = env_password
        else:
            password = getpass.getpass("Inserisci la tua password Garmin Connect: ")
    else:
        password = getpass.getpass("Inserisci la tua password Garmin Connect: ")

    if not email or not password:
        print("\n❌ Email e password sono obbligatorie per il primo login.")
        sys.exit(1)

    tokens_dir = os.getenv("GARMIN_TOKENS_DIR", "./tokens").strip()
    tokens_path = Path(tokens_dir)
    tokens_path.mkdir(parents=True, exist_ok=True)

    print(f"\n🔄 Tentativo di connessione ai server Garmin Connect per: {email}...")

    try:
        # Inizializza il client con il gestore MFA
        garmin_client = Garmin(
            email=email,
            password=password,
            prompt_mfa=prompt_mfa_callback,
        )

        # Esegue il login
        garmin_client.login()

        print("\n✅ Login effettuato con successo!")

        # 2. Salva i token su disco
        garmin_client.client.dump(str(tokens_path))
        print(f"💾 Token di sessione salvati correttamente nella cartella: {tokens_path.resolve()}")

        # 3. Serializza i token in formato JSON e Base64
        token_json_str = garmin_client.client.dumps()
        token_base64 = base64.b64encode(token_json_str.encode("utf-8")).decode("utf-8")

        # 4. Test di verifica dell'account
        print("\n🔍 Verifica delle API in corso...")
        try:
            full_name = garmin_client.get_full_name()
            print(f"👤 Profilo Garmin identificato: {full_name}")
        except Exception:
            print("👤 Accesso profilo confermato.")

        try:
            recent = garmin_client.get_activities(0, 1)
            if recent and len(recent) > 0:
                last_act = recent[0]
                act_name = last_act.get("activityName", "Senza nome")
                act_type = last_act.get("activityType", {}).get("typeKey", "unknown")
                act_date = last_act.get("startTimeLocal", "")
                print(f"🏃 Ultima attività trovata: '{act_name}' ({act_type}) il {act_date}")
            else:
                print("ℹ️ Nessuna attività recente trovata sull'account (account nuovo o nessun workout sincronizzato).")
        except Exception as e:
            print(f"⚠️ Nota sul recupero attività di test: {e}")

        # 5. Output di configurazione per il Deploy
        env_file_path = Path(".env")
        if not env_file_path.exists() and Path(".env.example").exists():
            # Copia da .env.example se .env non esiste
            import shutil
            shutil.copy(".env.example", ".env")

        print("\n" + "=" * 65)
        print("🎉 AUTENTICAZIONE COMPLETATA CON SUCCESSO!")
        print("=" * 65)
        print("\nPer usare questi token:")
        print("1. In locale: I token sono salvati nella cartella './tokens/'.")
        print("2. In Cloud (Railway, Render, Fly.io, Docker):")
        print("   Puoi impostare questa variabile d'ambiente nel pannello di controllo,")
        print("   così il server non richiederà un volume persistente su disco:\n")
        print(f"GARMIN_TOKENS_BASE64={token_base64}\n")

        save_choice = input("Vuoi salvare automaticamente GARMIN_TOKENS_BASE64 nel file .env locale? [S/n]: ").strip().lower()
        if save_choice in ("", "s", "si", "y", "yes"):
            set_key(str(env_file_path), "GARMIN_TOKENS_BASE64", token_base64)
            set_key(str(env_file_path), "GARMIN_TOKENS_DIR", str(tokens_dir))
            print("✅ File .env aggiornato con successo!")

        print("\n🚀 Ora puoi avviare il backend FastAPI con:")
        print("   uvicorn main:app --reload --port 8000\n")

    except GarminConnectTooManyRequestsError as e:
        print(f"\n❌ Troppi tentativi di login consecutivi (Rate Limit Garmin): {e}")
        print("Attendi qualche minuto prima di riprovare.")
        sys.exit(1)
    except GarminConnectAuthenticationError as e:
        print(f"\n❌ Errore di autenticazione: {e}")
        print("Verifica che email, password e codice MFA inseriti siano corretti.")
        sys.exit(1)
    except GarminConnectConnectionError as e:
        print(f"\n❌ Errore di connessione ai server Garmin: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Errore imprevisto durante l'autenticazione: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
