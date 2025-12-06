# bot.py
import os
import logging
import requests
import json
import re
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn
from rapidfuzz import process, fuzz

# ---------- إعداد السجلات ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING — Smart FAQ + Price Finder (data.txt)")

load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "my_verify_token_123")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
# Optional AI providers keys (if you want later)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Menu links (fallback when bot not sure)
MENU_LINKS = [
    "منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5",
    "منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8",
    "منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5",
    "منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5",
    "منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA",
    "منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8",
    "منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8",
    "جميع الكتالوجات: https://misrsweets.com/catalogs/"
]

# Files
DATA_FILE = "data.txt"
MEMORY_FILE = "memory.txt"
PAUSED_FILE = "paused_users.txt"

# thresholds
THRESHOLD_STRICT = 80   # لو الـ score >= 80 -> نعتبره مطابق
THRESHOLD_SUGGEST = 60  # لو بين 60 و 79 -> نقترح ونعطي خيار

app = FastAPI()

# ---------- Utilities ----------
def normalize_ar(text: str) -> str:
    text = text.lower().strip()
    # remove tashkeel and non-letter punctuation
    text = re.sub(r"[ًٌٍَُِّْـ]", "", text)  # basic tashkeel
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)  # keep arabic letters and numbers
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def load_data():
    """
    يقرأ data.txt ويُبني قاموس:
    key = normalized item name  => { 'name': original, 'category':..., 'unit':..., 'price': float }
    كذلك يبني قائمة بالأسماء للبحث fuzzy.
    """
    items = {}
    names_list = []
    if not os.path.exists(DATA_FILE):
        logger.warning("data.txt not found — the bot will still run but dataset is empty.")
        return items, names_list

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # format expected: Category | Code | Name | Unit | Price
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                # try alternative: Name — Price — Unit or csv style
                # safe fallback: split by "—"
                if "—" in line:
                    p2 = [p.strip() for p in line.split("—")]
                    if len(p2) >= 3:
                        name = p2[0]
                        price = p2[1]
                        unit = p2[2]
                        key = normalize_ar(name)
                        items[key] = {"name": name, "category": "", "unit": unit, "price": price}
                        names_list.append(name)
                        continue
                # skip malformed
                continue
            category, code, name, unit, price = parts[:5]
            key = normalize_ar(name)
            items[key] = {
                "name": name,
                "category": category,
                "code": code,
                "unit": unit,
                "price": price
            }
            names_list.append(name)
    return items, names_list

DATA_ITEMS, NAMES_LIST = load_data()

def save_paused_user(user_id: str):
    paused = set()
    if os.path.exists(PAUSED_FILE):
        with open(PAUSED_FILE, "r", encoding="utf-8") as f:
            paused = set(l.strip() for l in f if l.strip())
    paused.add(str(user_id))
    with open(PAUSED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(paused)))

def remove_paused_user(user_id: str):
    paused = set()
    if os.path.exists(PAUSED_FILE):
        with open(PAUSED_FILE, "r", encoding="utf-8") as f:
            paused = set(l.strip() for l in f if l.strip())
    paused.discard(str(user_id))
    with open(PAUSED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(paused)))

def is_paused(user_id: str) -> bool:
    if not os.path.exists(PAUSED_FILE):
        return False
    with open(PAUSED_FILE, "r", encoding="utf-8") as f:
        paused = set(l.strip() for l in f if l.strip())
    return str(user_id) in paused

def save_memory_record(record_type: str, content: str):
    """
    يحفظ دخول جديد في memory.txt حسب قواعد الذاكرة.
    صيغة السطر: 2025-12-06 — TYPE — "content"
    لا يحفظ بيانات شخصية.
    """
    # very simple sanitation: remove digits sequences that look like phone numbers
    content_sanitized = re.sub(r"\b0\d{8,}\b", "[PHONE_REMOVED]", content)
    ts = time.strftime("%Y-%m-%d")
    line = f"{ts} — {record_type} — \"{content_sanitized}\""
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info(f"Saved to memory: {line}")

def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.warning("PAGE_TOKEN not set. Skipping send_message.")
        return
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=8)
        logger.info(f"📤 Sent to {user_id} | status={r.status_code}")
        if r.status_code != 200:
            logger.error("FB send error: " + r.text)
    except Exception as e:
        logger.exception("FB send exception: " + str(e))

# ---------- Core reply logic ----------
def find_product(query: str):
    """
    ترجع: (mode, data)
    modes:
      - 'exact' => data is product dict
      - 'suggest' => data is (best_name, score)
      - 'none' => data None
    """
    if not DATA_ITEMS or not NAMES_LIST:
        return "none", None

    # normalize query
    qnorm = normalize_ar(query)

    # Try direct exact match first
    if qnorm in DATA_ITEMS:
        return "exact", DATA_ITEMS[qnorm]

    # fuzzy match (search among keys by using process.extractOne on original names)
    best = process.extractOne(query, NAMES_LIST, scorer=fuzz.WRatio)
    if best:
        name, score, idx = best  # rapidfuzz returns (choice, score, index)
        if score >= THRESHOLD_STRICT:
            key = normalize_ar(name)
            return "exact", DATA_ITEMS.get(key)
        elif score >= THRESHOLD_SUGGEST:
            return "suggest", (name, score)
    return "none", None

def format_price_response(item_dict):
    name = item_dict.get("name", "المنتج")
    price = item_dict.get("price", "غير متاح")
    unit = item_dict.get("unit", "Unit")
    emoji = "🧾"
    return f"{emoji} {name}\n💰 السعر: {price}\n📦 الوحدة: {unit}\n\nهل تحب تطلبه؟ أكتب \"طلب\" أو رقم الكمية."

def format_menu_links():
    text = "📋 تقدر تشوف المنيو الكامل هنا:\n"
    for l in MENU_LINKS:
        text += f"- {l}\n"
    text += "\n📩 لو عايز سعر صنف محدد اكتبه هنا بالاسم أو أقرب شكل ليه — وهتلاقي سعره."
    return text

# ---------- FastAPI endpoints ----------
@app.get("/")
def home():
    return {"status": "alive", "message": "Misr Sweets Bot — ready"}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified")
        return int(challenge)
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    # facebook page webhook structure
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                # ignore deliveries/read events
                if "message" not in msg:
                    continue
                if "text" not in msg["message"]:
                    continue
                sender = msg["sender"]["id"]
                text = msg["message"]["text"].strip()
                logger.info(f"👤 User {sender} says: {text}")

                # check paused
                if is_paused(sender):
                    logger.info(f"User {sender} is paused — ignoring message.")
                    # optionally respond to confirm pause
                    continue

                # control words (stop/resume) - Arabic and English forms
                tnorm = normalize_ar(text)
                if tnorm in ("stop", "وقف", "سكت", "ايقاف", "توقف"):
                    save_paused_user(sender)
                    send_message(sender, "⏸️ تم إيقاف الردود عليك مؤقتًا. لو عايز تكمل اكتب: resume أو استأنف.")
                    continue
                if tnorm in ("resume", "start", "استأنف", "ابدأ", "كمل"):
                    remove_paused_user(sender)
                    send_message(sender, "▶️ تم استئناف الردود. تحت أمرك 😊")
                    continue

                # menu request direct
                if any(w in tnorm for w in ("المنيو", "منيو", "قائمة", "كاتالوج", "menu")):
                    send_message(sender, format_menu_links())
                    continue

                # product search
                mode, data = find_product(text)
                if mode == "exact":
                    resp = format_price_response(data)
                    send_message(sender, resp)
                    # optional: if price not available, save memory candidate? skip automatic
                    continue
                elif mode == "suggest":
                    best_name, score = data
                    resp = (f"هل تقصد: «{best_name}»؟ (تشابه {int(score)}%)\n"
                            f"لو نعم اكتب: نعم {best_name}\nأو اكتب اسم المنتج بالكامل لو مش ده.")
                    send_message(sender, resp)
                    continue
                else:
                    # none -> fallback: ارسال المنيو أولاً ثم "سيتم التواصل معك"
                    menu = format_menu_links()
                    final = (f"{menu}\n\n📩 سيتم التواصل معك في أقرب وقت لو احتجنا تفاصيل إضافية. شكراً لتواصلك 😊")
                    send_message(sender, final)
                    # save candidate to memory as FAQ (but not personal data)
                    save_memory_record("FAQ_CANDIDATE", f"سؤال غير معروف: \"{text}\"")
                    continue

        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "ignored"})

# ---------- Admin helper endpoints (optional) ----------
@app.post("/admin/add_memory")
async def admin_add_memory(request: Request):
    body = await request.json()
    # expects: {"type":"PRICE_UPDATE","content":"..."}
    t = body.get("type")
    c = body.get("content")
    if not t or not c:
        raise HTTPException(status_code=400, detail="type and content required")
    save_memory_record(t, c)
    return {"status": "saved"}

# ---------- boot ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"Starting server on port {port}")
    uvicorn.run("bot:app", host="0.0.0.0", port=port, reload=False)