# bot.py
import os
import re
import time
import json
import logging
import httpx
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from difflib import SequenceMatcher
import unicodedata
import uvicorn

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")
logger.info("🚀 BOT RUNNING — FUZZY MATCH MODE (threshold 90%)")

# -------------------------
# Load env
# -------------------------
load_dotenv()
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")  # optional
MODEL = os.getenv("AI_MODEL", "mixtral-8x7b-32768")  # example

# -------------------------
# Globals & files
# -------------------------
DATA_FILE = "data.txt"
MEMORY_FILE = "memory.txt"

FUZZY_THRESHOLD = 0.90  # 90% similarity

# In-memory paused users (persisted in memory file)
paused_users = set()

app = FastAPI()

# -------------------------
# Utilities: Arabic normalization
# -------------------------
def normalize_ar(text: str) -> str:
    """Normalize Arabic text: lower, remove tashkeel, unify hamza, remove extra spaces."""
    if not text:
        return ""
    text = text.lower()
    # remove tashkeel (diacritics)
    text = ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.combining(ch))
    # replace hamza variants
    text = re.sub('[إأآا]', 'ا', text)
    text = re.sub('[ؤئ]', 'و', text)  # pragmatic simplification
    text = re.sub('ى', 'ي', text)
    # remove punctuation except * and numbers and letters
    text = re.sub(r'[^\w\s\*\u0600-\u06FF]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# -------------------------
# Load data.txt into dict
# -------------------------
products = {}  # name_normalized -> {name, price, unit, raw_name}
categories = {}  # optional grouping

def parse_price_str(s: str):
    """Try to extract a price float from string like '130.00' or '1,435.00'"""
    if not s:
        return None
    s = s.replace(',', '').strip()
    m = re.search(r'(\d+(\.\d+)?)', s)
    if m:
        try:
            return float(m.group(1))
        except:
            return None
    return None

def load_data():
    """Load data.txt and parse product lines into products dict."""
    products.clear()
    if not os.path.exists(DATA_FILE):
        logger.warning(f"data file {DATA_FILE} not found.")
        return

    text = open(DATA_FILE, "r", encoding="utf-8").read()
    # Try to parse lines like: "بسبوسة سادة: 130 — KG" or "بسبوسة سادة\tKG\t130.00"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines:
        # skip comments
        if line.startswith("#"):
            continue

        # pattern 1: name: price — UNIT
        m1 = re.match(r'^(?P<name>.+?)\s*[:\-]\s*(?P<price>[\d,\.]+)\s*(?:[—\-]\s*(?P<unit>\w+))?$', line)
        if m1:
            name = m1.group('name').strip()
            price = parse_price_str(m1.group('price'))
            unit = (m1.group('unit') or "").strip()
            add_product(name, price, unit)
            continue

        # pattern 2: name — price — Unit (with dashes)
        parts = [p.strip() for p in re.split(r'[-—\t]', line) if p.strip()]
        # heuristics
        if len(parts) >= 2:
            # Try find numeric in parts
            price = None
            unit = ""
            name = parts[0]
            for p in parts[1:]:
                if parse_price_str(p) is not None:
                    price = parse_price_str(p)
                elif p.isalpha():
                    unit = p
            add_product(name, price, unit)
            continue

        # pattern 3: CSV-like: name,unit,price or name,price,unit
        csv_parts = [p.strip() for p in re.split(r'[,\;]', line) if p.strip()]
        if len(csv_parts) >= 2:
            # try find price in any
            price = None
            name = csv_parts[0]
            unit = ""
            for p in csv_parts[1:]:
                if parse_price_str(p) is not None:
                    price = parse_price_str(p)
                else:
                    unit = p
            add_product(name, price, unit)
            continue

        # fallback: store line as name with no price
        add_product(line, None, "")

def add_product(name, price, unit):
    if not name:
        return
    raw = name.strip()
    norm = normalize_ar(raw)
    products[norm] = {"name": raw, "price": price, "unit": unit or ""}
    # Optionally group by category if name contains category keywords (not implemented heavy)

# -------------------------
# Memory file handling
# -------------------------
def load_memory():
    """Load memory file to restore paused users or other saved notes."""
    paused_users.clear()
    if not os.path.exists(MEMORY_FILE):
        # create with header
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write("[MEMORY]\n")
        return
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            # simple format: TYPE||user_id||content
            if line.startswith("PAUSE||"):
                parts = line.split("||", 2)
                if len(parts) >= 2:
                    try:
                        uid = parts[1].strip()
                        paused_users.add(uid)
                    except:
                        pass

def save_memory_pause(user_id):
    """Append PAUSE to memory file."""
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"PAUSE||{user_id}||{int(time.time())}\n")

def save_memory_unpause(user_id):
    """Remove PAUSE by rewriting file (simple implementation)."""
    if not os.path.exists(MEMORY_FILE):
        return
    lines = []
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    new_lines = []
    for ln in lines:
        if ln.startswith(f"PAUSE||{user_id}||"):
            continue
        new_lines.append(ln)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

# -------------------------
# Matching logic
# -------------------------
def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def find_best_match(query: str):
    """Return best match dict or None if below threshold."""
    q = normalize_ar(query)
    if not q:
        return None, 0.0

    # 1) exact contains (best)
    for key, info in products.items():
        if q == key or q in key or key in q:
            return info, 1.0

    # 2) startswith or word start
    for key, info in products.items():
        if key.startswith(q) or q.startswith(key):
            return info, 0.98

    # 3) fuzzy best ratio
    best = None
    best_score = 0.0
    for key, info in products.items():
        score = similarity(q, key)
        if score > best_score:
            best_score = score
            best = info
    if best_score >= FUZZY_THRESHOLD:
        return best, best_score
    return None, best_score

# -------------------------
# AI call (Groq) - optional
# -------------------------
async def call_groq(prompt: str, timeout=10):
    """Call Groq (if API key present). Returns text or None."""
    if not GROQ_API_KEY:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 300
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                j = r.json()
                # OpenAI-like response
                return j.get("choices", [])[0].get("message", {}).get("content", "").strip()
            else:
                logger.error(f"🔥 Groq Error Attempt {attempt+1}: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"⚠️ Groq exception: {e}")
        time.sleep(1)
    return None

# -------------------------
# Response formatting
# -------------------------
MENU_MESSAGE = (
    "📋 تقدر تشوف المنيو هنا:\n"
    "منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5\n"
    "منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8\n"
    "منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5\n"
    "منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5\n"
    "منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA\n"
    "منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8\n"
    "منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8\n"
    "📩 سيتم التواصل معك في أقرب وقت."
)

def format_product_response(p: dict) -> str:
    """Return Arabic nicely formatted response for product dict."""
    name = p.get("name", "المنتج")
    price = p.get("price")
    unit = p.get("unit") or ""
    if price is None:
        return f"🧾 **{name}**\n💰 السعر: غير متاح\n📦 الوحدة: {unit or 'غير متاح'}\n\n{MENU_MESSAGE}"
    # price formatting with thousands sep
    price_str = f"{price:,.0f}" if float(price).is_integer() else f"{price:,.2f}"
    return f"🧾 {name}\n💰 السعر: {price_str} جنيه\n📦 الوحدة: {unit or 'غير محدد'}\n\nهل تحب تطلبه؟ أكتب \"طلب\" أو اذكر الكمية. 😊"

# -------------------------
# Facebook send
# -------------------------
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.warning("No PAGE_TOKEN set — skipping actual send.")
        return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, json=payload, timeout=8)
        logger.info(f"📤 Sent to {user_id}: {text[:60]} | Status: {r.status_code}")
    except Exception as e:
        logger.error(f"Error sending message: {e}")

# -------------------------
# Main reply generator
# -------------------------
async def generate_reply(user_id: str, user_msg: str) -> str:
    # Check paused state
    if str(user_id) in paused_users:
        return "⏸️ البوت متوقف عندك حالياً. اكتب \"كمل\" أو \"resume\" لاستئناف المحادثة."

    text_norm = normalize_ar(user_msg)
    # Commands: stop / resume
    if text_norm in ("stop", "سكت", "وقف"):
        paused_users.add(str(user_id))
        save_memory_pause(user_id)
        return "🛑 تمام — البوت سكت عندك. اكتب \"كمل\" وقت ما تحب عشان أرجع. ✅"

    if text_norm in ("start", "resume", "كمل", "ابدأ"):
        if str(user_id) in paused_users:
            paused_users.discard(str(user_id))
            save_memory_unpause(user_id)
            return "▶️ رجعت تاني — تحت أمرك! 😊"
        else:
            return "🔔 البوت شغال بالفعل. كيف أقدر أساعدك؟"

    # If user asked for menu or catalogs explicitly
    if any(w in text_norm for w in ["منيو", "قائمة", "قیمت", "menyu", "catalog", "كatalog"]):
        return MENU_MESSAGE

    # Try to match product
    match, score = find_best_match(user_msg)
    if match:
        # If fuzzy match but not exact, mark nothing intrusive, just respond
        return format_product_response(match)

    # If not found, try AI helper to interpret (optional)
    # Build a prompt to ask AI to map to product names in data if available
    # but since we want strictness, we fallback to sending menu + contact promise
    # Optionally call Groq/OpenAI to rephrase and try again (light attempt)
    ai_prompt = (
        "أنت مساعد يقدم اقتراحات لمنتجات مطابقة من قائمة محددة.\n"
        "المستخدم كتب: " + user_msg + "\n"
        "إذا لم تستطع إيجاد منتج مطابق، أعد فقط الرد بالروابط للمنيو."
    )
    ai_response = await call_groq(ai_prompt)
    if ai_response:
        # try to extract any product-like token from AI response and try match again
        # simple heuristic: take last 1-3 words
        cand = ai_response.strip().split('\n')[0]
        if cand:
            match2, score2 = find_best_match(cand)
            if match2:
                return format_product_response(match2)

    # Default fallback: send menu and message
    return MENU_MESSAGE

# -------------------------
# FastAPI endpoints
# -------------------------
@app.get("/")
def home():
    return {"status": "alive", "mode": "fuzzy-90", "products_count": len(products)}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified.")
        return int(challenge)
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                sender = msg.get("sender", {}).get("id")
                # message text
                if "message" in msg and "text" in msg["message"]:
                    text = msg["message"]["text"]
                    logger.info(f"👤 User {sender} says: {text}")
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
                # postback or quick reply handling can be added here
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

# -------------------------
# Init load
# -------------------------
load_memory()
load_data()
logger.info(f"Loaded {len(products)} products from {DATA_FILE} and paused_users={len(paused_users)}")

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)