# 🏃 Garmin Connect AI Running Coach Backend (FastAPI + ChatGPT Action)

Backend REST in Python (FastAPI) progettato specificamente per collegare i dati di corsa e biometrici di **Garmin Connect** a un **Custom GPT di ChatGPT** tramite **OpenAPI Actions**.

Consente al tuo AI Coach su ChatGPT di accedere in tempo reale e in sola lettura ai tuoi allenamenti, split chilometrici, distribuzione nelle zone cardio (Z1-Z5), dinamiche di corsa, Body Battery, qualità del sonno e tempi di recupero.

---

## 📁 Struttura del Progetto

```text
garmin-gpt-backend/
├── main.py              # API FastAPI con sicurezza, schema OpenAPI e gestione endpoint
├── garmin_client.py     # Wrapper Garmin con gestione token, auto-reconnect e parsing snello
├── setup_auth.py        # CLI interattiva per primo login con email/password e supporto MFA (2FA)
├── test_endpoints.py    # Test di validazione automatica (autenticazione, parsing e risposte)
├── requirements.txt     # Dipendenze Python (FastAPI, uvicorn, garminconnect, garth, pydantic)
├── .env.example         # Template variabili d'ambiente
├── Dockerfile           # Immagine Docker containerizzata multi-platform (Python 3.12-slim)
├── .dockerignore        # File ignorati durante la build Docker
├── render.yaml          # Blueprint per deploy rapido su Render
├── Procfile             # Configurazione di avvio per Railway / Heroku
└── README.md            # Guida completa all'installazione, deploy e configurazione ChatGPT
```

---

## ⚡ 1. Installazione e Bootstrap Locale

### Prerequisiti
* Python 3.11 o 3.12
* Account Garmin Connect (anche con autenticazione a due fattori attiva)

### Passaggi:

1. **Clona o entra nella cartella del progetto:**
   ```bash
   cd garmin-gpt-backend
   ```

2. **Crea e attiva l'ambiente virtuale:**
   * **Linux/macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
   * **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```

3. **Installa le dipendenze:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Crea il file `.env` dal template:**
   ```bash
   cp .env.example .env
   ```
   *Modifica `API_KEY` con una password segreta complessa (es. generata con `python -c "import secrets; print(secrets.token_hex(24))"`).*

---

## 🔐 2. Primo Login e Gestione Token Garmin (Supporto MFA)

Garmin Connect richiede un primo accesso interattivo con supporto MFA (codice di verifica a 6 cifre via email/SMS). Lo script `setup_auth.py` automatizza l'intero processo:

```bash
python setup_auth.py
```

### Cosa fa lo script:
1. Chiede email e password Garmin (se presenti in `.env` le propone come default).
2. Se il tuo account ha il 2FA abilitato, chiede a console il **codice MFA ricevuto**.
3. Salva i token di sessione generati in `./tokens/garmin_tokens.json` con permessi restrittivi.
4. Genera una stringa `GARMIN_TOKENS_BASE64` che puoi salvare nel `.env`. **Questa stringa ti permette di fare il deploy su Render/Railway senza aver bisogno di dischi o volumi persistenti!**
5. Esegue un test rapido per verificare il profilo e l'ultima attività.

---

## 🚀 3. Avvio del Server Locale

Avvia FastAPI con Uvicorn:

```bash
uvicorn main:app --reload --port 8000
```

* **Documentazione Swagger interattiva:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **Schema OpenAPI per ChatGPT:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)
* **Informativa Privacy:** [http://localhost:8000/privacy](http://localhost:8000/privacy)

Puoi testare gli endpoint direttamente da Swagger UI inserendo la tua `API_KEY` cliccando sul pulsante verde **Authorize** in alto a destra.

---

## 🌐 4. Deploy Cloud per ChatGPT (Railway o Render)

OpenAI richiede che l'URL dell'Action sia **pubblico e con HTTPS**.

### Opzione A: Railway (Consigliata, più rapida)
1. Carica la cartella su una repository GitHub (privata).
2. Vai su [Railway.app](https://railway.app), clicca **New Project** -> **Deploy from GitHub repo**.
3. In **Variables**, imposta:
   * `API_KEY`: la tua chiave segreta
   * `SERVER_URL`: l'URL HTTPS assegnato da Railway (es. `https://garmin-gpt-production.up.railway.app`)
   * `GARMIN_TOKENS_BASE64`: la stringa Base64 generata da `setup_auth.py`
4. Il servizio si avvierà automaticamente usando il `Procfile` o il `Dockerfile`.

### Opzione B: Render
1. Vai su [Render.com](https://render.com) -> **New** -> **Blueprint**.
2. Collega la repo (utilizzerà `render.yaml`).
3. Inserisci le variabili d'ambiente `API_KEY`, `SERVER_URL` e `GARMIN_TOKENS_BASE64`.

### Opzione C: Test Rapido Locale tramite Tunnel (Cloudflare o Ngrok)
Se vuoi testare subito senza fare il deploy in cloud:
```bash
# Con Cloudflare Tunnel gratuito (nessun account necessario):
cloudflared tunnel --url http://localhost:8000
```
Copia l'URL `https://<subdomain>.trycloudflare.com` e impostalo come `SERVER_URL` in `.env`.

---

## 🤖 5. Configurazione della Custom Action in ChatGPT

### Passo 1: Crea o Modifica il tuo Custom GPT
1. Vai su [ChatGPT](https://chatgpt.com) -> Clicca sul tuo profilo -> **I miei GPT** -> **Crea un GPT**.
2. Nella scheda **Configura** (Configure):
   * **Nome:** `Coach Running AI` (o a piacere)
   * **Descrizione:** `Coach atletico personale collegato al tuo orologio Garmin.`

### Passo 2: Crea l'Action
1. Scorri in basso nella scheda Configura e clicca su **Crea nuova azione** (Create new action).
2. Alla voce **Schema**:
   * Clicca su **Importa da URL** (Import from URL) e inserisci:
     ```text
     https://tuo-dominio-pubblico.up.railway.app/openapi.json
     ```
   * *Oppure copia e incolla l'intero JSON ottenuto navigando su quell'indirizzo.*
   * ChatGPT rileverà automaticamente i 3 endpoint:
     - `getRecentActivities`
     - `getActivityDetails`
     - `getHealthSummary`
3. Alla voce **Autenticazione** (Authentication):
   * Clicca sull'icona dell'ingranaggio ⚙️ accanto ad Authentication.
   * **Tipo di autenticazione:** `API Key`
   * **Tipo di autenticazione API:** Seleziona `Custom`
   * **Nome intestazione personalizzata:** `X-API-Key`
   * **Chiave API:** Incolla il valore della tua `API_KEY` (definita in `.env`)
   * Clicca **Salva**.
4. Alla voce **Informativa sulla privacy** (Privacy Policy):
   * Inserisci: `https://tuo-dominio-pubblico.up.railway.app/privacy`

---

## 📋 6. Istruzioni / System Prompt Consigliato per il Custom GPT

Copia e incolla questo testo nel campo **Istruzioni** (Instructions) del tuo Custom GPT:

```markdown
Sei un Coach Atletico e di Corsa esperto, specializzato in fisiologia dell'esercizio, allenamento polarizzato (80/20) e preparazione atletica per mezzofondo, maratona e trail running.

Hai accesso diretto ai dati di allenamento e salute Garmin dell'atleta tramite le tue Action API integrate.

### REGOLE DI UTILIZZO DELLE ACTION:
1. Quando l'atleta chiede come sono andati gli ultimi allenamenti o che corse ha fatto di recente:
   - Invoca `getRecentActivities(limit=5, activity_type='running')`.
   - Analizza: passo medio, distanza, frequenza cardiaca media e massima, e soprattutto l'Aerobic ed Anaerobic Training Effect.
2. Quando l'atleta chiede un'analisi dettagliata di una corsa specifica:
   - Invoca `getActivityDetails(activity_id=...)`.
   - Esamina la tenuta del ritmo negli split chilometrici (pacing regolare vs negative split vs crollo finale).
   - Valuta la percentuale di tempo trascorsa nelle zone cardio (Z1-Z5): se era una corsa lenta, verifica che sia rimasto prevalentemente in Zona 2; se erano ripetute o tempo run, analizza il tempo in Z4/Z5.
   - Controlla le dinamiche di corsa se presenti: cadenza (ottimale 165-185 SPM), oscillazione verticale (< 8-9 cm indica ottima economia) e tempo di contatto con il suolo (GCT).
3. Quando l'atleta chiede "Come sto oggi?", "Posso allenarmi forte?", "Che allenamento mi consigli oggi?":
   - Invoca SEMPRE `getHealthSummary()`.
   - Verifica:
     * Frequenza cardiaca a riposo (RHR): se è sensibilmente più alta della media, indica affaticamento o stress sistemico.
     * Body Battery: livello attuale e carica notturna.
     * Sonno: ore totali, sleep score e percentuale di sonno profondo/REM.
     * Tempo di recupero consigliato (recovery_time_hours): se superiore a 24-36h, consiglia recupero attivo, mobilità o fondo lentissimo Z1/Z2.

### TONO E STILE:
- Sii incoraggiante, professionale, pragmatico e orientato ai dati.
- Non limitarti a ripetere i numeri che l'API restituisce: interpretali dal punto di vista dell'adattamento cardiovascolare e della prevenzione infortuni.
- Fornisci sempre un consiglio pratico e azionabile (es. alimentazione, idratazione, allunghi, stretching o regolazione del passo).
```

---

## 🧪 7. Esecuzione dei Test Unitari

Per verificare l'integrità del codice e della sicurezza in qualsiasi momento:

```bash
python -m unittest test_endpoints.py
```
