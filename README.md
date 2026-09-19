# ✈️ FlightAlertBot — Notifiche istantanee voli sotto prezzo

Bot Telegram che gira **24/7 su Render** (sempre attivo con auto-deploy da GitHub). Imposti range tempo (es. maggio e giugno) e durata (1,2,3,14 giorni) + prezzo max, e appena un volo scende sotto soglia ti arriva **notifica istantanea su Telegram con 4 link reali** (Google Flights, Skyscanner, KAYAK).

## Come funziona
- Tu: `/alert` → Milano → Tokyo → `maggio,giugno` → `1,2,3,14` → `250€`
- Bot controlla ogni 30 min (configurabile) tutte le date di maggio e giugno per durate 1,2,3,14 giorni
- Appena trova es. `Milano→Tokyo 12/05→26/05 (14gg) = 238€` ≤ 250€ → **notifica istantanea**:
  ```
  🚨 PREZZO SOTTO SOGLIA!
  ✈️ Milano → Tokyo
  📅 12/05/2026 → 26/05/2026 (14 giorni)
  💰 238€ (soglia 250€)
  🔗 Google Flights | Skyscanner | KAYAK
  ```

## Stack
- **Bot**: `python-telegram-bot` + ConversationHandler (5 step)
- **Server**: Flask (`/`) health + `/webhook/<token>` per Telegram webhook su Render
- **Scheduler**: APScheduler ogni `CHECK_INTERVAL_MIN` (default 30)
- **Prezzi**: `MOCK_MODE=true` = random realistico per demo (certi cali sotto soglia). Con chiavi `SERPAPI_KEY` o `AMADEUS_API_KEY` usa API reali.
- **Storage**: `alerts.json` (MVP). Su Render con disk persistente o passa a Postgres (vedi sotto).
- **Deploy**: GitHub → Render autoDeploy (pull & push commit)

## Deploy completo in 10 minuti

### 1. Crea repo GitHub
```bash
cd FlightAlertBot
git init
git add .
git commit -m "FlightAlertBot v1"
# Crea repo su github.com/new (es. tuo-username/flightalertbot) poi:
git remote add origin https://github.com/TUOUSER/flightalertbot.git
git branch -M main
git push -u origin main
```

### 2. Crea bot Telegram
1. Apri Telegram → cerca `@BotFather` → `/newbot` → nome `FlightAlertBot` → copia **TOKEN** (es. `123456:ABC...`)
2. (Opzionale) `/mybots` → tuo bot → `Edit Bot` → `Edit Description` ecc.

### 3. Deploy su Render (sempre attivo)
1. Vai su **render.com** → New → **Web Service** → Connect GitHub repo `flightalertbot`
2. Impostazioni:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app_flask --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
   - Oppure seleziona `render.yaml` (Infrastructure as Code) → Render lo legge automaticamente
3. **Environment Variables** (Render → Environment):
   - `BOT_TOKEN` = token di @BotFather (obbligatorio)
   - `WEBHOOK_URL` = `https://TUO-APP.onrender.com` (dopo primo deploy, Render ti dà URL es. `https://flightalertbot.onrender.com` → incollalo qui e fai Save → Render redeploy)
   - `MOCK_MODE` = `true` (demo) o `false` (per API reali)
   - `CHECK_INTERVAL_MIN` = `30`
   - (Opzionale reali) `SERPAPI_KEY` = tua chiave serpapi.com (Google Flights) o `AMADEUS_API_KEY` + `AMADEUS_API_SECRET` (test.api.amadeus.com)
4. Deploy → attendi 2 min → apri `https://TUO-APP.onrender.com/` → dovresti vedere `{"status":"ok",...}`
5. Imposta webhook (una volta):
   - Apri browser: `https://TUO-APP.onrender.com/webhook/set` → deve rispondere `{"ok":true}`
   - Oppure il bot lo fa da solo al boot se `WEBHOOK_URL` è settato
6. Per tenerlo **sempre attivo su piano Free** (che dorme dopo 15 min senza traffico): vai su **uptimerobot.com** → Add Monitor → HTTP(s) → URL `https://TUO-APP.onrender.com/` → ogni 5 min → così Render non dorme.

### 4. Usa il bot
Telegram → cerca il tuo bot → `/start` → `/alert` → segui 5 domande.
Esempio:
```
/alert
Milano
Tokyo
maggio,giugno
1,2,3,14
250
```
Poi `/list` per vedere alert, `/check` per forzare controllo immediato, `/stop 1` per disattivare.

### 5. Workflow Git pull & push (auto-deploy)
Ogni modifica che fai in locale:
```bash
git add .
git commit -m "aggiunto nuovo aeroporto"
git push origin main
```
Render vede il push su GitHub (autoDeploy: true) e **fa deploy automatico in 1-2 minuti** senza toccare Render. Pull per aggiornare locale:
```bash
git pull origin main
```

### 6. Passare a prezzi reali (opzionale)
- **SerpApi Google Flights** (più fedele a Google): registrati su serpapi.com → prendi API key → mettila in Render env `SERPAPI_KEY` → imposta `MOCK_MODE=false` → redeploy. Costo: 50 ricerche gratis/mese.
- **Amadeus** (test gratis): developers.amadeus.com → crea app → prendi `API Key` + `Secret` → metti `AMADEUS_API_KEY` + `AMADEUS_API_SECRET` → `MOCK_MODE=false`.
- Se nessuna chiave, resta in **MOCK** (prezzi random realistici con cali 18% sotto soglia per demo notifiche).

### 7. Persistenza su Render Free
`alerts.json` su disco effimero si azzera ad ogni deploy. Soluzioni:
- **Render Disk** (a pagamento): Add Disk → mount `/app` → `alerts.json` persiste
- **Postgres gratis** (consigliato): Render → New → PostgreSQL → prendi `DATABASE_URL` → modifica `app.py` per usare `psycopg2` invece di json (già predisposto: se `DATABASE_URL` esiste passa a DB)
- Per ora MVP usa json e avvisa in `/list` se resettato.

## Comandi bot
- `/start` — benvenuto
- `/alert` — crea alert (5 step: partenza, destinazione, mesi, durate, prezzo)
- `/list` — lista alert tuoi
- `/stop <id>` — disattiva
- `/check` — forza controllo ora
- `/help` — aiuto

## Test locale
```bash
pip install -r requirements.txt
$env:BOT_TOKEN="tuo-token"
$env:MOCK_MODE="true"
python app.py
# apri Telegram → parla al bot → polling locale
```

## Sicurezza
- Non committare mai `BOT_TOKEN` nel repo! Usa env su Render.
- `.gitignore` già esclude `alerts.json` e `.env`

---
Creato per ItinerarioPerfetto — FlightAlertBot v1 • Notifiche istantanee con link reali • Render + GitHub auto-deploy
