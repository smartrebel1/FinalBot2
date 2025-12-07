import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
import time
from rapidfuzz import process, fuzz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING - MisrSweets Bot (local)")

load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "my_verify_token_123")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Add other API keys if you want fallbacks

MENU_LINKS_FILE = "data/menu_links.txt"
DATA_FILE = "data/raw_data.txt"
MEMORY_FILE = "data/memory.txt"

EMOJI = "🍰"

app = FastAPI()

def load_data():
    pairs = []
    if not os.path.exists(DATA_FILE):
        return pairs
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # name heuristics
            if len(parts) >= 3:
                name = parts[2]
            elif len(parts) >= 2:
                name = parts[1]
            else:
                name = parts[0]
            price = None
            unit = ""
            # find price token from the end
            for token in parts[::-1]:
                token_norm = token.replace(",", "").replace("جنيه","").replace("EGP","").replace("egp","").strip()
                try:
                    if token_norm.replace(".","",1).isdigit():
                        price = token_norm
                        break
                except:
                    pass
            if len(parts) >= 4:
                unit = parts[3]
            pairs.append((name, {"line": line, "price": price, "unit": unit}))
    return pairs

def load_menu_links():
    if not os.path.exists(MENU_LINKS_FILE):
        return []
    with open(MENU_LINKS_FILE, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]

def smart_lookup(query, data_pairs, limit=5):
    names = [p[0] for p in data_pairs]
    if not names:
        return []
    results = process.extract(query, names, scorer=fuzz.token_sort_ratio, limit=limit)
    matches = []
    for match, score, idx in results:
        entry = data_pairs[idx][1]
        matches.append({"name": match, "score": score, "line": entry["line"], "price": entry.get("price"), "unit": entry.get("unit")})
    return matches

def format_price_reply(match):
    if match["price"]:
        return f"🧾 {match['name']}\n💰 السعر: {match['price']} جنيه\n📦 الوحدة: {match['unit'] or 'غير محددة'}\n"
    else:
        return f"🧾 {match['name']}\n💰 السعر: غير متاح\n📦 الوحدة: {match['unit'] or 'غير محددة'}\n"

def append_memory(entry_line):
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry_line.strip() + "\n")
    except Exception as e:
        logger.error("Memory write error: %s", e)

@app.get("/")
def home():
    return {"status": "alive", "note": "MisrSweets Bot"}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403)

async def generate_reply(user_msg: str):
    user_msg = user_msg.strip()
    data = load_data()
    menu_links = load_menu_links()

    # Commands
    cmd = user_msg.lower()
    if cmd in ["stop", "سكت", "قف", "توقف"]:
        return "🛑 تم إيقاف البوت — سأصمت الآن. لو حبيت تكمل، اكتب `resume` أو `استأنف`."
    if cmd in ["resume", "start", "استأنف", "ابدأ"]:
        return "✅ تم تفعيل البوت مرة أخرى. كيف أقدر أساعدك؟ " + EMOJI

    # fuzzy lookup
    matches = smart_lookup(user_msg, data, limit=3)
    if matches and matches[0]["score"] >= 85:
        # confident
        reply = format_price_reply(matches[0])
        # store into memory a PRICE_UPDATE example (optional)
        append_memory(f"{time.strftime('%Y-%m-%d')} — QUERY_MATCH — \"{matches[0]['name']} -> {matches[0].get('price') or 'N/A'}\"")
        reply += f"\n📋 المنيو الكامل: {menu_links[0] if menu_links else 'رابط المنيو غير متاح'}"
        return reply
    elif matches and matches[0]["score"] >= 55:
        # propose options
        reply = "ممكن تقصد واحد من دول؟\n"
        for m in matches:
            reply += f"- {m['name']} ({m['score']}%)\n"
        reply += "\nلو عايز السعر دقيق اكتب اسم المنتج بالكامل أو اختار واحد من الفوق. " + EMOJI
        return reply
    else:
        # unknown -> send menu links first (user requested this behaviour)
        reply = "📋 المنيو الكامل هنا — اختار الصنف اللي تحب أو انسخ/اكتب اسمه بالتحديد:\n"
        for link in menu_links:
            reply += f"{link}\n"
        reply += "\n📩 سيتم التواصل معك في أقرب وقت لو احتجنا توضيح. " + EMOJI
        return reply

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info("📩 Incoming Event: %s", body)
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    logger.info("👤 User %s says: %s", sender, text)
                    reply = await generate_reply(text)
                    send_message(sender, reply)
        return JSONResponse({"status":"ok"}, status_code=200)
    return JSONResponse({"status":"ignored"}, status_code=200)

def send_message(user_id, text):
    if not PAGE_TOKEN:
        logger.warning("No PAGE_TOKEN set — skipping send_message.")
        return
    url = f"https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=8)
        logger.info("📤 Sent: %s | Status: %s", text[:80], r.status_code)
    except Exception as e:
        logger.error("Send message failed: %s", e)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
