# Garmin Connect AI Running Coach Backend (FastAPI + MCP Plugin)

Sviluppato da AK Dev.

Backend in Python basato su FastAPI e Model Context Protocol (MCP) per connettere la telemetria e i dati biometrici di Garmin Connect a ChatGPT, utilizzabile sia come plugin MCP nativo che come Custom Action OpenAPI.

L'applicazione espone in sola lettura i dati telemetrici dell'atleta:
* Analisi delle corse: split chilometrici lap per lap, tempo e percentuali nelle zone cardiache (Z1-Z5), dinamiche di corsa (cadenza, oscillazione verticale, tempo di contatto al suolo).
* Metriche di prestazione: VO2 Max (stima e valore preciso), età di fitness rispetto all'età anagrafica, previsioni ufficiali dei tempi di gara (5 km, 10 km, mezza maratona, maratona) con calcolo dei ritmi al chilometro.
* Fisiologia e recupero: variabilità della frequenza cardiaca (HRV status e intervallo di baseline), stato dell'allenamento, carico acuto e cronico (ACWR), Body Battery, frequenza cardiaca a riposo (RHR), analisi delle fasi del sonno e tempo di recupero consigliato.
* Biometria quotidiana: passi, calorie attive e basali, minuti di intensità moderata e vigorosa, piani saliti, livelli di stress e frequenza respiratoria.

---

## Struttura del repository

```text
garmin-gpt-backend/
├── main.py              # Applicazione FastAPI, routing REST ed endpoint SSE/Streamable HTTP
├── mcp_server.py        # Definizione del server MCP e registrazione degli strumenti per ChatGPT
├── garmin_client.py     # Client Garmin Connect con gestione sessione, auto-refresh e modelli Pydantic
├── setup_web.py         # Interfaccia web per primo login e gestione codice MFA
├── setup_auth.py        # Script CLI alternativo per autenticazione iniziale
├── test_endpoints.py    # Test di validazione automatica degli endpoint e della sicurezza
├── requirements.txt     # Dipendenze applicative (FastAPI, uvicorn, mcp, garminconnect, garth)
├── .env.example         # Esempio di configurazione delle variabili d'ambiente
├── Dockerfile           # Immagine container multi-platform (Python 3.12-slim)
├── .dockerignore        # File esclusi dal build del container
├── render.yaml          # Blueprint di deployment per Render
└── Procfile             # Configurazione di avvio per piattaforme cloud PaaS
```

---

## Architettura e sicurezza

L'autenticazione con Garmin Connect impiega il protocollo OAuth con token di sessione persistenti (refresh token).

I token di sessione vengono serializzati in una stringa Base64 (`GARMIN_TOKENS_BASE64`). Questa architettura consente il deployment su ambienti serverless o container cloud effimeri senza richiedere volumi persistenti su disco o la memorizzazione di credenziali in chiaro.

Il file `.gitignore` impedisce il tracciamento di token locali, file `.env` o credenziali private nel repository Git.

---

## Deployment su cloud (Render)

Il deployment su Render consente di mantenere il server attivo 24 ore su 24 con certificato HTTPS gestito.

### 1. Creazione del servizio web
1. Accedere alla dashboard di [render.com](https://render.com).
2. Selezionare **New +** e scegliere **Web Service**.
3. Selezionare l'opzione **Build and deploy from a Git repository**.
4. Inserire l'URL pubblico del repository:
   ```text
   https://github.com/AnasKabba/garmin-mcp-coach
   ```
5. Cliccare su **Connect**.

### 2. Parametri di configurazione
* **Name**: `garmin-mcp-coach` (o un identificativo a scelta)
* **Region**: `Frankfurt (EU Central)`
* **Branch**: `main`
* **Runtime**: `Python 3`
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
* **Instance Type**: `Free`

### 3. Variabili d'ambiente
Nella sezione **Environment Variables**, configurare le seguenti chiavi:

| Variabile | Descrizione | Obbligatoria |
| :--- | :--- | :--- |
| `API_KEY` | Chiave segreta personalizzata per proteggere le comunicazioni | Sì |
| `GARMIN_TOKENS_BASE64` | Stringa Base64 dei token generata in fase di primo accesso | Sì |
| `SERVER_URL` | URL pubblico del servizio generato da Render (es. `https://garmin-mcp-coach.onrender.com`) | Consigliata |

Al termine dell'inserimento, cliccare su **Deploy Web Service**. Il container verrà compilato e avviato in circa 60-90 secondi.

---

## Deployment alternativo con Docker

Il repository include un `Dockerfile` ottimizzato basato su `python:3.12-slim`.

Per compilare ed eseguire il container in ambiente cloud o host dedicato:

```bash
docker build -t garmin-mcp-coach .

docker run -d \
  -p 8000:8000 \
  -e API_KEY="tua_chiave_segreta" \
  -e GARMIN_TOKENS_BASE64="stringa_base64_dei_token" \
  --name garmin-coach \
  garmin-mcp-coach
```

---

## Collegamento a ChatGPT

### Configurazione come plugin MCP (consigliata)
1. Aprire ChatGPT nel browser e accedere alla sezione **Plugin** (o integrazioni).
2. Cliccare su **Aggiungi un server MCP personalizzato**.
3. Compilare i campi come segue:
   * **Nome**: `Garmin Running Coach by AK Dev.`
   * **Descrizione**: `Analisi corse, split km, zone cardio, VO2 Max, sonno e recupero da Garmin Connect.`
   * **Connessione**: selezionare **URL del server** e inserire l'endpoint del servizio cloud:
     ```text
     https://tuo-servizio.onrender.com/mcp
     ```
   * **Autenticazione**: selezionare `Nessuna` (la sicurezza è garantita dall'isolamento del servizio).
4. Accettare la dichiarazione di sicurezza e cliccare su **Crea come plugin**.

### Configurazione alternativa come Custom Action OpenAPI (Custom GPT)
Se si desidera utilizzare il servizio all'interno di un Custom GPT:
1. Accedere all'editor del Custom GPT in **Configura** > **Crea nuova azione**.
2. Nel campo **Schema**, selezionare **Importa da URL** e inserire:
   ```text
   https://tuo-servizio.onrender.com/openapi.json
   ```
3. Nelle impostazioni di autenticazione dell'azione:
   * **Tipo di autenticazione**: `API Key`
   * **Tipo di chiave**: `Custom`
   * **Nome intestazione**: `X-API-Key`
   * **Valore**: la chiave inserita nella variabile `API_KEY`.
4. Nel campo **Informativa sulla privacy**:
   ```text
   https://tuo-servizio.onrender.com/privacy
   ```

---

## Strumenti MCP disponibili

Il server registra quattro strumenti ad alta densità informativa:

1. **`get_performance_metrics`**
   * VO2 Max (valore arrotondato, valore preciso con decimali, categoria aerobica).
   * Età di fitness stimata e confronto con l'età anagrafica.
   * Previsioni dei tempi di gara Garmin per 5 km, 10 km, mezza maratona e maratona con passo medio al chilometro calcolato.
   * Stato dell'allenamento, carico acuto, intervallo di carico cronico e indice ACWR.
   * Stato della variabilità cardiaca (HRV) con media settimanale e dell'ultima notte.

2. **`get_health_summary`**
   * Frequenza cardiaca a riposo (RHR) e valori minimi/massimi giornalieri.
   * Livello di Body Battery attuale, punti ricaricati durante il sonno e punti consumati.
   * Analisi del sonno: punteggio qualitativo (0-100), durata totale e suddivisione in sonno profondo, REM, leggero e veglia.
   * Tempo di recupero consigliato in ore e punteggio di Training Readiness.
   * Biometria giornaliera: passi, chilometri camminati, calorie attive e basali, minuti di intensità, piani saliti, livello di stress medio e frequenza respiratoria.

3. **`get_recent_activities`**
   * Elenco delle ultime sessioni registrate con distanza, durata attiva, passo medio al chilometro, dislivello positivo e negativo, frequenza cardiaca media e massima, cadenza e Training Effect aerobico/anaerobico.

4. **`get_activity_details`**
   * Analisi approfondita di una singola corsa tramite `activity_id`.
   * Split chilometrici con tempo parziale, passo, frequenza cardiaca media e dislivello di ogni frazione.
   * Tempo e percentuale trascorsi in ciascuna delle 5 zone di frequenza cardiaca (Z1-Z5).
   * Dinamiche di corsa avanzate: cadenza in passi al minuto, oscillazione verticale del busto e tempo di contatto con il suolo.

---

## Test automatizzati

Per eseguire la suite di test unitari sul codice:

```bash
python -m unittest test_endpoints.py
```

I test verificano l'isolamento della sicurezza degli endpoint, la corretta gestione delle chiavi API e la conformità degli schemi JSON restituiti.

---

## Autore e licenza

* **Sviluppo**: AK Dev.
* **Repository**: [github.com/AnasKabba/garmin-mcp-coach](https://github.com/AnasKabba/garmin-mcp-coach)
* **Licenza**: MIT
