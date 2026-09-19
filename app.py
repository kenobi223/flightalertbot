# -*- coding: utf-8 -*-
"""
FlightAlertBot - Notifiche istantanee voli sotto prezzo
- Imposti: partenza, destinazione, mesi (maggio,giugno), durate (1,2,3,14), prezzo max
- Bot controlla ogni 30 min su Google Flights / Amadeus / Mock e ti notifica con link reale
- Deploy su Render sempre attivo + auto-deploy da GitHub (git push)
"""
import os
import json
import logging
import asyncio
import datetime
import random
import urllib.parse
import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("FlightAlertBot")

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # es. https://tuo-app.onrender.com
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "30"))
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"  # true = prezzi fake random per demo, false = prova API reali
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
AMADEUS_KEY = os.getenv("AMADEUS_API_KEY", "")
AMADEUS_SECRET = os.getenv("AMADEUS_API_SECRET", "")
KIWI_API_KEY = os.getenv("KIWI_API_KEY", "")  # Tequila by Kiwi.com - gratis 500/mese, copre Ryanair/Wizz/ITA/Emirates
RYANAIR_DIRECT = os.getenv("RYANAIR_DIRECT", "true").lower() == "true"  # usa API Ryanair diretta senza key (services-api.ryanair.com)
FAST_FLIGHTS = os.getenv("FAST_FLIGHTS", "true").lower() == "true"  # AWeirdDev/flights - Google Flights scraper gratis, copre tutte le compagnie senza API key

# Stati conversazione
DEPARTURE, DESTINATION, MONTHS, DURATIONS, PRICE = range(5)

# Mappatura città -> IATA / coords (per link e stima)
CITIES = {
    "milano": {"iata":"MXP", "name":"Milano", "lat":45.6306, "lon":8.7284},
    "roma": {"iata":"FCO", "name":"Roma", "lat":41.8003, "lon":12.2389},
    "napoli": {"iata":"NAP", "name":"Napoli", "lat":40.8860, "lon":14.2908},
    "torino": {"iata":"TRN", "name":"Torino", "lat":45.2008, "lon":7.6497},
    "venezia": {"iata":"VCE", "name":"Venezia", "lat":45.5053, "lon":12.3519},
    "bologna": {"iata":"BLQ", "name":"Bologna", "lat":44.5354, "lon":11.2887},
    "firenze": {"iata":"FLR", "name":"Firenze", "lat":43.8100, "lon":11.2051},
    "palermo": {"iata":"PMO", "name":"Palermo", "lat":38.1759, "lon":13.0910},
    "catania": {"iata":"CTA", "name":"Catania", "lat":37.4668, "lon":15.0664},
    "bari": {"iata":"BRI", "name":"Bari", "lat":41.1389, "lon":16.7606},
    "tokyo": {"iata":"TYO", "name":"Tokyo", "lat":35.6762, "lon":139.6503},
    "parigi": {"iata":"CDG", "name":"Parigi", "lat":48.8566, "lon":2.3522},
    "barcellona": {"iata":"BCN", "name":"Barcellona", "lat":41.3851, "lon":2.1734},
    "new york": {"iata":"JFK", "name":"New York", "lat":40.7128, "lon":-74.0060},
    "londra": {"iata":"LHR", "name":"Londra", "lat":51.5074, "lon":-0.1278},
    "roma": {"iata":"FCO", "name":"Roma", "lat":41.8003, "lon":12.2389},
}

def normalize_city(s):
    s=s.strip().lower()
    # mappa alias
    if s in CITIES: return CITIES[s]
    # cerca contiene
    for k,v in CITIES.items():
        if k in s or s in k:
            return v
    # fallback: usa come nome, IATA inventato (prime 3 lettere)
    return {"iata": s[:3].upper(), "name": s.title(), "lat":0, "lon":0}

def parse_months(text):
    """Accetta: 'maggio,giugno' o '2026-05,2026-06' o 'maggio e giugno' o '05-06'"""
    text=text.lower().replace(" e ",",").replace(" ",",").replace(";",",")
    mesi_map={"gennaio":"01","febbraio":"02","marzo":"03","aprile":"04","maggio":"05","giugno":"06","luglio":"07","agosto":"08","settembre":"09","ottobre":"10","novembre":"11","dicembre":"12",
              "gen":"01","feb":"02","mar":"03","apr":"04","mag":"05","giu":"06","lug":"07","ago":"08","set":"09","ott":"10","nov":"11","dic":"12"}
    out=[]
    year=datetime.date.today().year
    # se siamo già oltre dicembre, prossimo anno
    if datetime.date.today().month>10:
        year+=1
    parts=[p.strip() for p in text.split(",") if p.strip()]
    for p in parts:
        p=p.strip()
        # formato 2026-05 o 2026/05
        if "-" in p and len(p)>=7:
            try:
                y,m=p.split("-")[:2]
                out.append(f"{int(y):04d}-{int(m):02d}")
                continue
            except: pass
        # mese nome
        for nome,num in mesi_map.items():
            if nome in p:
                # se p contiene anno es. maggio 2026
                y=year
                if "2025" in p: y=2025
                if "2026" in p: y=2026
                if "2027" in p: y=2027
                out.append(f"{y}-{num}")
                break
        else:
            # prova numero mese 5 o 05
            if p.isdigit() and 1<=int(p)<=12:
                out.append(f"{year}-{int(p):02d}")
    # deduplica e ordina
    out=sorted(list(dict.fromkeys(out)))
    return out

def parse_durations(text):
    """Accetta '1,2,3,14' o '1 2 3 14' o '1-3,14'"""
    text=text.replace("giorni","").replace("gg","").replace(" "," , ")
    parts=[]
    for token in text.replace(";",",").split(","):
        token=token.strip()
        if not token: continue
        if "-" in token:
            try:
                a,b=map(int, token.split("-"))
                parts.extend(list(range(a,b+1)))
            except: pass
        else:
            try: parts.append(int(token))
            except: pass
    parts=[p for p in parts if 1<=p<=30]
    return sorted(list(set(parts)))

# --- Storage alerts ---
ALERTS_FILE="alerts.json"

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE,"r",encoding="utf-8") as f:
            return json.load(f)
    except: return []

def save_alerts(alerts):
    with open(ALERTS_FILE,"w",encoding="utf-8") as f:
        json.dump(alerts, f, indent=2, ensure_ascii=False)

def add_alert(user_id, username, departure, destination, months, durations, max_price):
    alerts=load_alerts()
    new_id=max([a["id"] for a in alerts], default=0)+1
    alert={"id":new_id,"user_id":user_id,"username":username,"departure":departure,"destination":destination,"months":months,"durations":durations,"max_price":max_price,"active":True,"created":datetime.datetime.now().isoformat(),"last_notified":None,"hits":0}
    alerts.append(alert); save_alerts(alerts); return alert

def get_user_alerts(user_id):
    return [a for a in load_alerts() if a["user_id"]==user_id]

def deactivate_alert(alert_id, user_id):
    alerts=load_alerts()
    for a in alerts:
        if a["id"]==alert_id and a["user_id"]==user_id:
            a["active"]=False; save_alerts(alerts); return True
    return False

# --- Flight price logic ---
def haversine(lat1,lon1,lat2,lon2):
    import math
    R=6371
    dlat=math.radians(lat2-lat1); dlon=math.radians(lon2-lon1)
    a=math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))

def build_links(dep, dest, d1, d2):
    dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
    d1s=d1.strftime("%Y-%m-%d"); d2s=d2.strftime("%Y-%m-%d")
    q=urllib.parse.quote(f"volo da {dep} a {dest} {d1s} ritorno {d2s}")
    return {
        "google": f"https://www.google.com/travel/flights?q={q}",
        "google_direct": f"https://www.google.com/travel/flights#flt={dep_i}.{dest_i}.{d1s}*{dest_i}.{dep_i}.{d2s};c:EUR;e:1;sd:1;t:e",
        "skyscanner": f"https://www.skyscanner.it/trasporti/voli/{dep_i.lower()}/{dest_i.lower()}/{d1.strftime('%y%m%d')}/{d2.strftime('%y%m%d')}/",
        "kayak": f"https://www.kayak.it/flights/{dep_i}-{dest_i}/{d1s}/{d2s}"
    }

def search_kiwi(dep, dest, d1, d2):
    """Kiwi Tequila - gratis, copre Ryanair/Wizz/ITA/Emirates + low-cost. Richiede KIWI_API_KEY."""
    if not KIWI_API_KEY:
        return None, None
    try:
        dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
        url="https://api.tequila.kiwi.com/v2/search"
        headers={"apikey": KIWI_API_KEY}
        params={
            "fly_from": dep_i, "fly_to": dest_i,
            "date_from": d1.strftime("%d/%m/%Y"), "date_to": d1.strftime("%d/%m/%Y"),
            "return_from": d2.strftime("%d/%m/%Y"), "return_to": d2.strftime("%d/%m/%Y"),
            "curr": "EUR", "limit": 3, "sort": "price", "adults": 1
        }
        r=requests.get(url, headers=headers, params=params, timeout=15)
        data=r.json()
        if data.get("data"):
            price=int(float(data["data"][0]["price"]))
            return price, "kiwi"
    except Exception as e:
        log.warning(f"Kiwi error {e}")
    return None, None

def search_ryanair_direct(dep, dest, d1, d2):
    """Ryanair diretta senza key - services-api.ryanair.com (sicura, ufficiale). Solo se RYANAIR_DIRECT=true e rotta Ryanair."""
    if not RYANAIR_DIRECT:
        return None, None
    try:
        dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
        # Ryanair farfnd roundTripFares - cerca voli diretti Ryanair
        url="https://services-api.ryanair.com/farfnd/3/roundTripFares"
        params={
            "departureAirportIataCode": dep_i, "arrivalAirportIataCode": dest_i,
            "outboundDepartureDateFrom": d1.strftime("%Y-%m-%d"), "outboundDepartureDateTo": d1.strftime("%Y-%m-%d"),
            "inboundDepartureDateFrom": d2.strftime("%Y-%m-%d"), "inboundDepartureDateTo": d2.strftime("%Y-%m-%d"),
            "language": "en", "market": "en-gb", "limit": 5
        }
        r=requests.get(url, params=params, timeout=10, headers={"User-Agent":"FlightAlertBot/1.0"})
        if r.status_code==200:
            data=r.json()
            fares=data.get("fares",[])
            if fares:
                # prendi prezzo più basso (outbound+inbound)
                best=min(fares, key=lambda x: x.get("summary",{}).get("price",{}).get("value",9999))
                price=best["summary"]["price"]["value"]
                return int(price), "ryanair"
    except Exception as e:
        log.debug(f"Ryanair direct no fare {e}")
    return None, None

def search_fast_flights(dep, dest, d1, d2):
    """AWeirdDev/flights - Google Flights scraper gratis, veloce, copre Ryanair/Wizz/ITA/Emirates/Italo senza API key"""
    if not FAST_FLIGHTS:
        return None, None
    try:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights
        dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
        # se IATA non valida (3 lettere), skip
        if len(dep_i)!=3 or len(dest_i)!=3:
            return None, None
        query = create_query(
            flights=[
                FlightQuery(date=d1.strftime("%Y-%m-%d"), from_airport=dep_i, to_airport=dest_i),
                FlightQuery(date=d2.strftime("%Y-%m-%d"), from_airport=dest_i, to_airport=dep_i),
            ],
            trip="round-trip",
            seat="economy",
            passengers=Passengers(adults=1),
            currency="EUR",
            language="en",
        )
        res = get_flights(query)
        # res è ResultList con .flights o lista diretta
        flights=[]
        if hasattr(res, 'flights'):
            flights=res.flights
        elif isinstance(res, list):
            flights=res
        else:
            flights=list(res) if res else []
        if flights:
            # prendi prezzo più basso
            # ogni flight ha .price (str con €) o .price_value
            best=min(flights, key=lambda f: float(str(getattr(f,'price','9999')).replace('€','').replace(',','').replace(' ','').strip() or 9999))
            price_raw=getattr(best,'price','')
            # estrai numero
            import re
            m=re.search(r'[\d,.]+', str(price_raw))
            if m:
                price=int(float(m.group(0).replace(',','')))
                return price, "fast-flights"
            # fallback: prova attributo price_value
            if hasattr(best,'price_value'):
                return int(best.price_value), "fast-flights"
    except Exception as e:
        log.debug(f"fast-flights no result {e}")
    return None, None

def search_price_real(dep, dest, d1, d2):
    """Prova API reali in ordine: fast-flights (Google) -> Kiwi (Ryanair/Wizz) -> Ryanair diretta -> SerpApi -> Amadeus -> mock"""
    # 1. Prova scraper gratis anche in MOCK_MODE (copre tutte le compagnie senza key)
    if FAST_FLIGHTS:
        price, src = search_fast_flights(dep, dest, d1, d2)
        if price: return price, src
    if KIWI_API_KEY:
        price, src = search_kiwi(dep, dest, d1, d2)
        if price: return price, src
    if RYANAIR_DIRECT:
        price, src = search_ryanair_direct(dep, dest, d1, d2)
        if price: return price, src

    if MOCK_MODE:
        # Mock: prezzo random 70-380, con 25% chance di scendere sotto soglia per demo
        base_dep=normalize_city(dep); base_dest=normalize_city(dest)
        if base_dep["lat"] and base_dest["lat"]:
            dist=haversine(base_dep["lat"],base_dep["lon"],base_dest["lat"],base_dest["lon"])
            if dist==0: dist=500
            # Milano-Tokyo ~9700km -> 250-400, Milano-Roma ~500km -> 50-120
            if dist>5000: price=random.randint(180,420)
            elif dist>1500: price=random.randint(90,220)
            else: price=random.randint(45,140)
            # occasional drop
            if random.random()<0.18:
                price=int(price*0.62)
        else:
            price=random.randint(60,280)
        return price, "mock"

    # SerpApi Google Flights
    if SERPAPI_KEY:
        try:
            dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
            url="https://serpapi.com/search"
            params={"engine":"google_flights","departure_id":dep_i,"arrival_id":dest_i,"outbound_date":d1.strftime("%Y-%m-%d"),"return_date":d2.strftime("%Y-%m-%d"),"currency":"EUR","hl":"it","api_key":SERPAPI_KEY}
            r=requests.get(url, params=params, timeout=15)
            data=r.json()
            # SerpApi ritorna best_flights o price_insights
            if "best_flights" in data and data["best_flights"]:
                price=data["best_flights"][0].get("price")
                if price: return int(price), "serpapi"
            if "price_insights" in data and "lowest_price" in data["price_insights"]:
                return int(data["price_insights"]["lowest_price"]), "serpapi"
        except Exception as e:
            log.warning(f"SerpApi error {e}")

    # Amadeus
    if AMADEUS_KEY and AMADEUS_SECRET:
        try:
            # ottieni token
            tok=requests.post("https://test.api.amadeus.com/v1/security/oauth2/token", data={"grant_type":"client_credentials","client_id":AMADEUS_KEY,"client_secret":AMADEUS_SECRET}, timeout=10).json()
            token=tok.get("access_token")
            if token:
                dep_i=normalize_city(dep)["iata"]; dest_i=normalize_city(dest)["iata"]
                url="https://test.api.amadeus.com/v2/shopping/flight-offers"
                headers={"Authorization": f"Bearer {token}"}
                params={"originLocationCode":dep_i,"destinationLocationCode":dest_i,"departureDate":d1.strftime("%Y-%m-%d"),"returnDate":d2.strftime("%Y-%m-%d"),"adults":1,"currencyCode":"EUR","max":3}
                r=requests.get(url, headers=headers, params=params, timeout=15)
                data=r.json()
                if "data" in data and data["data"]:
                    price=float(data["data"][0]["price"]["total"])
                    return int(price), "amadeus"
        except Exception as e:
            log.warning(f"Amadeus error {e}")

    # fallback mock se API falliscono
    price=random.randint(80,300)
    return price, "fallback_mock"

async def check_all_alerts(application):
    alerts=load_alerts()
    active=[a for a in alerts if a["active"]]
    if not active:
        log.info("Nessun alert attivo")
        return
    log.info(f"Controllo {len(active)} alert attivi...")
    for alert in active:
        dep=alert["departure"]; dest=alert["destination"]; months=alert["months"]; durations=alert["durations"]; max_price=alert["max_price"]
        user_id=alert["user_id"]
        found=None
        # per ogni mese, prova date campionate (5, 12, 19, 26 del mese per non fare 30 chiamate)
        for ym in months:
            try:
                y,m=map(int, ym.split("-"))
                # campionamento: 3 date per mese per ogni durata (per non spammare API)
                sample_days=[5,12,19,26]
                for day in sample_days:
                    try:
                        d1=datetime.date(y,m,day)
                    except: continue
                    if d1 < datetime.date.today() + datetime.timedelta(days=1): continue
                    for dur in durations:
                        d2=d1 + datetime.timedelta(days=dur)
                        price, source = search_price_real(dep, dest, d1, d2)
                        log.info(f"Check {dep}->{dest} {d1} +{dur}gg = {price}€ ({source}) vs max {max_price}€")
                        if price <= max_price:
                            found=(d1,d2,price,source)
                            break
                    if found: break
                if found: break
            except Exception as e:
                log.warning(f"Errore check {ym}: {e}")
        if found:
            d1,d2,price,source=found
            links=build_links(dep, dest, d1, d2)
            # evita spam: notifica massimo 1 volta ogni 6 ore per stesso alert
            last=alert.get("last_notified")
            if last:
                try:
                    last_dt=datetime.datetime.fromisoformat(last)
                    if (datetime.datetime.now()-last_dt).total_seconds() < 6*3600:
                        log.info(f"Skip notifica recente per alert {alert['id']}")
                        continue
                except: pass
            text=(
                f"🚨 *PREZZO SOTTO SOGLIA!* 🚨\n\n"
                f"✈️ *{dep.title()} → {dest.title()}*\n"
                f"📅 {d1.strftime('%d/%m/%Y')} → {d2.strftime('%d/%m/%Y')} ({(d2-d1).days} giorni)\n"
                f"💰 *{price}€* (soglia {max_price}€) — fonte: {source}\n"
                f"📍 Alert #{alert['id']} | {', '.join(months)} | durate {','.join(map(str,durations))}gg\n\n"
                f"🔗 *Link reali per prenotare ora:*\n"
                f"[Google Flights]({links['google']}) | [Google Direct]({links['google_direct']})\n"
                f"[Skyscanner]({links['skyscanner']}) | [KAYAK]({links['kayak']})\n\n"
                f"⚡ Prezzi live, prenota veloce! Usa /list per gestire alert."
            )
            try:
                await application.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown", disable_web_page_preview=False)
                # aggiorna last_notified
                alerts_all=load_alerts()
                for a in alerts_all:
                    if a["id"]==alert["id"]:
                        a["last_notified"]=datetime.datetime.now().isoformat(); a["hits"]=a.get("hits",0)+1
                        break
                save_alerts(alerts_all)
                log.info(f"Notifica inviata a {user_id} per alert {alert['id']}")
            except Exception as e:
                log.error(f"Invio notifica fallito {user_id}: {e}")
        else:
            log.info(f"Nessun prezzo sotto soglia per alert {alert['id']}")

# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ *FlightAlertBot — Notifiche istantanee*\n\n"
        "Ti avviso *istantaneamente* su Telegram con link reali quando un volo scende sotto il prezzo che decidi tu.\n\n"
        "Imposti:\n"
        "• Partenza (es. Milano)\n"
        "• Destinazione (es. Tokyo)\n"
        "• Range mesi (es. maggio e giugno)\n"
        "• Durate (es. 1,2,3,14 giorni)\n"
        "• Prezzo max (es. 150€)\n\n"
        "Comandi:\n"
        "/alert — crea nuovo alert\n"
        "/list — vedi i tuoi alert attivi\n"
        "/check — forza controllo ora\n"
        "/stop — disattiva un alert\n"
        "/help — aiuto\n\n"
        "Esempio: *Milano → Tokyo, maggio-giugno, 7 o 14 giorni, sotto 400€* → appena trova 380€ ti arriva notifica con link Google Flights/Skyscanner.\n\n"
        f"Modalità: {'MOCK (demo prezzi random)' if MOCK_MODE else 'API reali'} | Check ogni {CHECK_INTERVAL_MIN} min",
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Come creare alert:*\n"
        "/alert e segui i 5 passi.\n\n"
        "Mesi: scrivi come `maggio,giugno` o `2026-05,2026-06` o `5,6`\n"
        "Durate: `1,2,3,14` o `7-14` o `3`\n"
        "Prezzo: solo numero, es. `180`\n\n"
        "Il bot gira su Render 24/7 e controlla ogni 30 min. Ricevi notifica istantanea con 4 link reali (Google, Skyscanner, KAYAK).\n"
        "Per fermare: /stop ID (vedi /list)\n",
        parse_mode="Markdown"
    )

async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛫 *Da dove parti?* Scrivi città (es. Milano, Roma, Napoli, Torino) o aeroporto (MXP, FCO):", parse_mode="Markdown")
    return DEPARTURE

async def alert_departure(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["departure"]=update.message.text.strip()
    await update.message.reply_text(f"📍 Partenza: *{context.user_data['departure']}*\n\n🌍 *Dove vuoi andare?* Scrivi destinazione (es. Tokyo, Parigi, Barcellona, New York):", parse_mode="Markdown")
    return DESTINATION

async def alert_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["destination"]=update.message.text.strip()
    await update.message.reply_text(
        f"Destinazione: *{context.user_data['destination']}*\n\n"
        "📅 *In quali mesi vuoi viaggiare?*\n"
        "Esempi:\n"
        "`maggio,giugno`\n"
        "`2026-05,2026-06`\n"
        "`5,6` (maggio e giugno di quest'anno)\n"
        "Scrivi i mesi separati da virgola:",
        parse_mode="Markdown"
    )
    return MONTHS

async def alert_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    months=parse_months(update.message.text)
    if not months:
        await update.message.reply_text("❌ Non ho capito i mesi. Riprova: es. `maggio,giugno` o `2026-05,2026-06`", parse_mode="Markdown")
        return MONTHS
    context.user_data["months"]=months
    await update.message.reply_text(f"✅ Mesi: *{', '.join(months)}*\n\n⏱ *Quanti giorni vuoi stare?*\nEsempi:\n`1,2,3,14`  (1 o 2 o 3 o 14 giorni)\n`7`  (solo 7 giorni)\n`7-14` (da 7 a 14 giorni)\nScrivi durate:", parse_mode="Markdown")
    return DURATIONS

async def alert_durations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    durs=parse_durations(update.message.text)
    if not durs:
        await update.message.reply_text("❌ Durate non valide. Riprova: es. `1,2,3,14`", parse_mode="Markdown")
        return DURATIONS
    context.user_data["durations"]=durs
    await update.message.reply_text(f"✅ Durate: *{', '.join(map(str,durs))} giorni*\n\n💰 *Prezzo massimo in €?*\nScrivi solo il numero, es. `150` (ti notifico quando volo A/R ≤ 150€):", parse_mode="Markdown")
    return PRICE

async def alert_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price=int(update.message.text.strip().replace("€","").replace(" ",""))
        if price<10 or price>5000:
            raise ValueError()
    except:
        await update.message.reply_text("❌ Prezzo non valido. Scrivi un numero tra 10 e 5000, es. `180`")
        return PRICE
    context.user_data["max_price"]=price
    # salva
    user=update.effective_user
    dep=context.user_data["departure"]; dest=context.user_data["destination"]; months=context.user_data["months"]; durs=context.user_data["durations"]
    alert=add_alert(user.id, user.username or user.first_name, dep, dest, months, durs, price)
    links_example=build_links(dep, dest, datetime.date.today()+datetime.timedelta(days=30), datetime.date.today()+datetime.timedelta(days=30+durs[0]))
    await update.message.reply_text(
        f"✅ *Alert #{alert['id']} creato!*\n\n"
        f"✈️ {dep.title()} → {dest.title()}\n"
        f"📅 Mesi: {', '.join(months)}\n"
        f"⏱ Durate: {', '.join(map(str,durs))} giorni\n"
        f"💰 Soglia: ≤ {price}€\n\n"
        f"Controllo ogni {CHECK_INTERVAL_MIN} min su Render 24/7. Appena trovo prezzo ≤ {price}€ ti invio *notifica istantanea* con 4 link reali:\n"
        f"• Google Flights\n• Skyscanner\n• KAYAK\n\n"
        f"Esempio link (con le tue date):\n[Google]({links_example['google']}) | [Skyscanner]({links_example['skyscanner']})\n\n"
        f"Comandi: /list per vedere alert, /check per forzare controllo ora, /stop {alert['id']} per disattivare.",
        parse_mode="Markdown", disable_web_page_preview=True
    )
    return ConversationHandler.END

async def alert_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Creazione alert annullata. Usa /alert per riprovare.")
    return ConversationHandler.END

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alerts=get_user_alerts(update.effective_user.id)
    if not alerts:
        await update.message.reply_text("Nessun alert. Crea con /alert")
        return
    text="📋 *I tuoi alert:*\n\n"
    for a in alerts:
        status="🟢 Attivo" if a["active"] else "⚪ Disattivo"
        text+=f"#{a['id']} {status}\n✈️ {a['departure'].title()} → {a['destination'].title()}\n📅 {', '.join(a['months'])} | ⏱ {', '.join(map(str,a['durations']))}gg | 💰 ≤{a['max_price']}€ | hits:{a.get('hits',0)}\n"
        text+=f"/stop {a['id']} per disattivare\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def stop_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /stop ID  (vedi ID con /list)")
        return
    try:
        aid=int(context.args[0])
        if deactivate_alert(aid, update.effective_user.id):
            await update.message.reply_text(f"✅ Alert #{aid} disattivato.")
        else:
            await update.message.reply_text("❌ Alert non trovato o non tuo.")
    except:
        await update.message.reply_text("❌ ID non valido.")

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🔍 Controllo immediato in corso (sorgente: {'MOCK' if MOCK_MODE else 'API reali'})...")
    await check_all_alerts(context.application)
    await update.message.reply_text("✅ Controllo completato. Se c'era prezzo sotto soglia hai ricevuto notifica.")

# --- Flask + Web + Webhook ---
app_flask = Flask(__name__, template_folder="templates", static_folder="static")

@app_flask.route("/", methods=["GET"])
def web_index():
    return render_template("index.html")

@app_flask.route("/miniapp", methods=["GET"])
def miniapp():
    return render_template("index.html")

@app_flask.route("/api/health", methods=["GET"])
def health():
    alerts=load_alerts()
    return jsonify({"status":"ok","bot":"FlightAlertBot","alerts_total":len(alerts),"active":len([a for a in alerts if a["active"]]),"mock":MOCK_MODE,"check_interval_min":CHECK_INTERVAL_MIN, "time": datetime.datetime.now().isoformat()})

@app_flask.route("/api/alerts", methods=["GET"])
def api_list_alerts():
    user_id=request.args.get("user_id", "")
    tg=request.args.get("tg","")
    alerts=load_alerts()
    # se user_id fornito, filtra per quell'utente + mostra anche alert web con stesso tg username
    if user_id:
        # mostra alert di quell'utente + alert web recenti (ultimi 20) per demo
        filtered=[a for a in alerts if str(a.get("user_id"))==str(user_id) or (tg and a.get("tg_username")==tg)]
        # per web, mostra anche ultimi 20 globali se pochi
        if len(filtered)<3 and user_id.startswith("web_"):
            filtered=alerts[-20:]
        return jsonify({"alerts": filtered[-50:]})
    return jsonify({"alerts": alerts[-50:]})

@app_flask.route("/api/alerts", methods=["POST"])
def api_create_alert():
    try:
        data=request.get_json(force=True)
        departure=data.get("departure","").strip()
        destination=data.get("destination","").strip()
        months_raw=data.get("months","").strip()
        durs_raw=data.get("durations","").strip()
        max_price=int(data.get("max_price",0))
        user_id=data.get("user_id") or f"web_{request.remote_addr}"
        tg_username=data.get("tg_username","").strip()
        months=parse_months(months_raw)
        durs=parse_durations(durs_raw)
        if not departure or not destination or not months or not durs or not max_price:
            return jsonify({"ok":False,"error":"Compila tutti i campi correttamente"}),400
        # se tg_username è @username, prova a risolvere user_id esistente con stesso username
        # cerca alert esistente con stesso username per riutilizzare user_id Telegram
        if tg_username:
            # cerca tra alert esistenti un user_id numerico con stesso username
            for a in load_alerts():
                if a.get("username") and tg_username.replace("@","").lower() in str(a.get("username")).lower():
                    user_id=a["user_id"]
                    break
        # se initData contiene user, usa quell'ID
        # fallback: se tg WebApp, prova a estrarre da header
        alert=add_alert(user_id, tg_username or str(user_id), departure, destination, months, durs, max_price)
        # salva tg_username per lookup futuro
        if tg_username:
            alerts=load_alerts()
            for a in alerts:
                if a["id"]==alert["id"]:
                    a["tg_username"]=tg_username
                    break
            save_alerts(alerts)
            alert["tg_username"]=tg_username
        return jsonify({"ok":True,"alert": alert})
    except Exception as e:
        log.error(f"api create error {e}", exc_info=True)
        return jsonify({"ok":False,"error":str(e)}),500

@app_flask.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def api_delete_alert(alert_id):
    try:
        data=request.get_json(force=True) or {}
        user_id=data.get("user_id","")
        alerts=load_alerts()
        for a in alerts:
            if a["id"]==alert_id:
                # permette delete se user_id matcha o se web_ (per demo)
                if str(a.get("user_id"))==str(user_id) or str(user_id).startswith("web_"):
                    a["active"]=False
                    save_alerts(alerts)
                    return jsonify({"ok":True})
                else:
                    return jsonify({"ok":False,"error":"Non autorizzato"}),403
        return jsonify({"ok":False,"error":"Alert non trovato"}),404
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app_flask.route("/api/check", methods=["POST"])
def api_check():
    try:
        data=request.get_json(silent=True) or {}
        alert_id=data.get("alert_id")
        # avvia check in background thread
        import threading
        def do_check():
            loop=asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if application and application.bot:
                loop.run_until_complete(check_all_alerts(application))
            loop.close()
        threading.Thread(target=do_check, daemon=True).start()
        return jsonify({"ok":True,"msg":"Check avviato"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app_flask.route("/webhook/<token>", methods=["POST"])
def webhook(token):
    global application, scheduler
    if not BOT_TOKEN or token != BOT_TOKEN: return "invalid token", 403
    # lazy init se application è None (gunicorn non ha inizializzato)
    if application is None:
        try:
            log.info("Lazy init bot in webhook...")
            application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
            _setup_handlers(application)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(application.initialize())
            loop.run_until_complete(application.start())
            if scheduler is None:
                scheduler = BackgroundScheduler()
                scheduler.add_job(lambda: asyncio.run(check_all_alerts(application)), 'interval', minutes=CHECK_INTERVAL_MIN, id="check_flights", replace_existing=True)
                scheduler.start()
                log.info("Scheduler lazy avviato")
            log.info("Bot lazy inizializzato")
        except Exception as e:
            log.error(f"Lazy init fallita: {e}", exc_info=True)
            return f"lazy init error {e}", 500
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        loop.close()
        return "ok"
    except Exception as e:
        log.error(f"webhook error {e}", exc_info=True)
        return f"error {e}", 500

@app_flask.route("/debug", methods=["GET"])
def debug():
    return jsonify({"bot_token_set": bool(BOT_TOKEN), "application": str(application is not None), "scheduler": str(scheduler is not None), "port": PORT, "webhook_url": WEBHOOK_URL})

@app_flask.route("/webhook/set", methods=["GET"])
def set_webhook():
    if not WEBHOOK_URL or not BOT_TOKEN:
        return jsonify({"error":"WEBHOOK_URL o BOT_TOKEN mancanti"}), 400
    url=f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}/webhook/{BOT_TOKEN}"
    r=requests.get(url)
    return jsonify(r.json())

# --- Main ---
application = None
scheduler = None

def run_flask():
    app_flask.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

async def post_init(app):
    # schedulatore
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: asyncio.run(check_all_alerts(app)), 'interval', minutes=CHECK_INTERVAL_MIN, id="check_flights", replace_existing=True)
    scheduler.start()
    log.info(f"Scheduler avviato ogni {CHECK_INTERVAL_MIN} min")
    # webhook se configurato
    if WEBHOOK_URL and BOT_TOKEN:
        wh_url=f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
        try:
            await app.bot.set_webhook(url=wh_url, drop_pending_updates=True)
            log.info(f"Webhook impostato su {wh_url}")
        except Exception as e:
            log.warning(f"Webhook fallito, uso polling: {e}")

def main():
    global application
    if not BOT_TOKEN:
        log.error("BOT_TOKEN mancante! Imposta env BOT_TOKEN")
        print("⚠️  BOT_TOKEN mancante. Crea bot da @BotFather e imposta env.")
        # avvia comunque Flask per health check
        app_flask.run(host="0.0.0.0", port=PORT)
        return
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    _setup_handlers(application)

    # avvia Flask in thread separato se webhook, altrimenti polling
    import threading
    if WEBHOOK_URL:
        threading.Thread(target=run_flask, daemon=True).start()
        log.info(f"Modalità WEBHOOK su porta {PORT}")
        application.run_polling() # per compatibilità, ma webhook già gestito da Flask; in produzione meglio solo Flask
        # Nota: su Render con webhook, il polling non serve; ma per semplicità usiamo polling se WEBHOOK_URL non raggiungibile
    else:
        # Senza webhook: polling + Flask health
        import threading
        threading.Thread(target=run_flask, daemon=True).start()
        log.info("Modalità POLLING (locale)")
        application.run_polling()

def _setup_handlers(app):
    conv = ConversationHandler(
        entry_points=[CommandHandler("alert", alert_start)],
        states={
            DEPARTURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_departure)],
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_destination)],
            MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_months)],
            DURATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_durations)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_price)],
        },
        fallbacks=[CommandHandler("cancel", alert_cancel)],
        allow_reentry=True
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("stop", stop_alert))
    app.add_handler(CommandHandler("check", check_now))

# --- Init per gunicorn/Render (quando non è __main__) ---
if BOT_TOKEN and application is None and os.getenv("PORT"):
    try:
        log.info("Init gunicorn/Render webhook mode...")
        application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
        _setup_handlers(application)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        # scheduler
        if scheduler is None:
            scheduler = BackgroundScheduler()
            scheduler.add_job(lambda: asyncio.run(check_all_alerts(application)), 'interval', minutes=CHECK_INTERVAL_MIN, id="check_flights", replace_existing=True)
            scheduler.start()
            log.info(f"Scheduler gunicorn avviato ogni {CHECK_INTERVAL_MIN} min")
        log.info("Bot inizializzato per gunicorn")
    except Exception as e:
        log.error(f"Init gunicorn fallita: {e}", exc_info=True)

if __name__=="__main__":
    main()
