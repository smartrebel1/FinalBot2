# bot.py
import os
import json
import logging
import time
import re
from pathlib import Path
from difflib import get_close_matches
from typing import Dict, Tuple, Optional, List

import requests
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bot")
logger.info("🚀 BOT STARTING — Local-first product lookup (Arabic replies, minimal emojis)")

# ---------- env ----------
load_dotenv()
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)  # optional

# ---------- files ----------
DATA_FILE = Path("data.txt")
MEMORY_FILE = Path("memory.json")

# ---------- default menu links (fallback) ----------
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

# ---------- memory helpers ----------
def ensure_memory() -> Dict:
    if not MEMORY_FILE.exists():
        base = {"paused_users": {}, "unknown_queries": []}
        MEMORY_FILE.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        return base
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if "paused_users" not in data:
            data["paused_users"] = {}
        if "unknown_queries" not in data:
            data["unknown_queries"] = []
        return data
    except Exception as e:
        logger.error("Error reading memory.json, recreating: %s", e)
        base = {"paused_users": {}, "unknown_queries": []}
        MEMORY_FILE.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        return base

def save_memory(mem: Dict):
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")

memory = ensure_memory()

# ---------- parse data.txt ----------
def parse_line_for_item(line: str) -> Optional[Tuple[str, Optional[float], str]]:
    """
    حاول استخراج اسم/سعر/وحدة من سطر نصى.
    يرجع (name, price_or_None, unit_str)
    """
    line = line.strip()
    if not line:
        return None

    # استخرج السعر: رقم قد يحتوي على فاصلة أو نقطة (نبحث عن آخر رقم)
    price_match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)(?!.*\d)", line)
    price = None
    if price_match:
        try:
            price = float(price_match.group(1).replace(",", "."))
        except:
            price = None

    # detect unit near price or common units
    unit = ""
    unit_match = re.search(r"\b(KG|كيلو|كجم|جم|جرام|Unit|unit|قطعة|Unit)\b", line, re.IGNORECASE)
    if unit_match:
        unit = unit_match.group(0)

    # determine name: remove price and unit, remove codes (numbers at start)
    cleaned = re.sub(r"\b\d+[\d\.\,]*\b", "", line)  # remove numbers
    # split by tabs or multiple spaces or dashes
    parts = [p.strip() for p in re.split(r"\t+|\s{2,}|\s-\s|\s—\s|:|\||,", cleaned) if p.strip()]
    # choose the part with arabic letters or longest
    name_candidates = [p for p in parts if re.search(r"[\u0600-\u06FFA-Za-z]", p)]
    name = name_candidates[0] if name_candidates else (parts[0] if parts else line)
    # final cleanup
    name = name.strip(" -:؛،")

    return (name, price, unit)

def load_data() -> Dict[str, Dict]:
    index = {}
    if not DATA_FILE.exists():
        logger.warning("data.txt not found. index empty.")
        return index
    text = DATA_FILE.read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    for line in lines:
        parsed = parse_line_for_item(line)
        if parsed:
            name, price, unit = parsed
            if name:
                index[name] = {"price": price, "unit": unit}
    logger.info("Loaded %d items from data.txt", len(index))
    return index

data_index = load_data()
all_names = list(data_index.keys())

# reload function to call after updating data.txt without restarting if needed
def reload_data():
    global data_index, all_names
    data_index = load_data()
    all_names = list(data_index.keys())

# ---------- search helpers ----------
def find_product(name: str) -> Tuple[Optional[str], Optional[float], Optional[str], List[str]]:
    """
    يحاول إيجاد تطابق مباشر، فرعي، ثم fuzzy.
    returns (matched_name or None, price, unit, suggestions_list)
    """
    name_clean = name.strip().lower()
    # exact match
    for k in all_names:
        if k.strip().lower() == name_clean:
            info = data_index.get(k, {})
            return k, info.get("price"), info.get("unit"), []

    # substring matches
    substr = [k for k in all_names if name_clean in k.lower()]
    if substr:
        k = substr[0]
        info = data_index.get(k, {})
        return k, info.get("price"), info.get("unit"), substr[:6]

    # fuzzy using difflib
    close = get_close_matches(name, all_names, n=6, cutoff=0.6)
    if close:
        k = close[0]
        info = data_index.get(k, {})
        return k, info.get("price"), info.get("unit"), close

    return None, None, None, []

# ---------- formatting replies ----------
def format_price_reply(name: str, price: Optional[float], unit: Optional[str]) -> str:
    if price is None:
        # name exists but price missing
        menu_text = "\n".join(MENU_LINKS)
        return (
            f"📋 المنيو الكامل:\n{menu_text}\n\n"
            f"✳️ بالنسبة لـ «{name}» السعر غير مضاف حالياً.\n"
            f"📩 سيتم التواصل معك قريبًا للتأكيد."
        )
    # format price nicely
    if price == int(price):
        price_str = f"{int(price)}"
    else:
        price_str = f"{price:.2f}"
    unit_str = unit or "وحدة"
    return f"✅ {name}\n💰 السعر: {price_str} ج\n📦 الوحدة: {unit_str}\nلو تحب تضيفه للطلب اكتب: طلب {name} ✅"

def menu_reply_short() -> str:
    # Short menu header with minimal emojis (as requested)
    menu_text = "\n".join(MENU_LINKS)
    return f"📋 تقدر تشوف المنيو هنا:\n{menu_text}\n\n✳️ لو محتاج سعر صنف معين اكتب اسمه بالضبط أو أقرب شكل ليه.\n📩 سيتم التواصل معك لو احتجنا توضيح."

# ---------- OpenAI optional ----------
async def call_openai_chat(prompt: str) -> Optional[str]:
    if not OPENAI_API_KEY:
        return None
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "system", "content": "أنت مساعد خدمة عملاء عربي مصري مختصر."},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 300,
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            else:
                logger.warning("OpenAI returned %s: %s", r.status_code, r.text)
        except Exception as e:
            logger.error("OpenAI call error: %s", e)
        time.sleep(1)
    return None

# ---------- pause / memory ----------
def is_paused(user_id: str) -> bool:
    mem = ensure_memory()
    return bool(mem.get("paused_users", {}).get(str(user_id), False))

def set_paused(user_id: str, value: bool):
    mem = ensure_memory()
    mem["paused_users"][str(user_id)] = bool(value)
    save_memory(mem)

def log_unknown_query(user_id: str, text: str):
    mem = ensure_memory()
    mem.setdefault("unknown_queries", []).append({"user": str(user_id), "text": text, "ts": int(time.time())})
    save_memory(mem)

# ---------- main reply generator ----------
async def generate_reply(user_id: str, user_msg: str) -> str:
    msg = user_msg.strip()
    if not msg:
        return menu_reply_short()

    # control commands
    stop_cmds = ["stop", "قف", "وقف", "توقف"]
    start_cmds = ["start", "ابدأ", "استأنف", "استئناف"]
    low = msg.lower().strip()
    if low in stop_cmds:
        set_paused(user_id, True)
        return "⛔ تم إيقاف الردود عندك. اكتب 'ابدأ' لو عايز البوت يشتغل تاني."
    if low in start_cmds:
        set_paused(user_id, False)
        return "✅ رجعت تاني! جاهز أساعدك."

    if is_paused(user_id):
        return "🔕 البوت متوقف عندك. اكتب 'ابدأ' لو حابب ترجعه."

    # direct requests for menu / list
    if re.search(r"\b(المنيو|منيو|قائمة|قائمة|menu|أسعار)\b", msg, re.IGNORECASE):
        return menu_reply_short()

    # request for branches/times
    if re.search(r"\b(فروع|فرع|مواعيد|ساعات|توصيل|delivery|رقم)\b", msg, re.IGNORECASE):
        return (
            "🕒 **مواعيد العمل**: جميع الأيام من 8 ص إلى 10 م (الخميس والجمعة حتى 11 م).\n"
            "🏬 **فروع**: طنطا - ميدان الساعة: 0403335941 / 0403335942\n"
            "الإسكندرية - محطة الرمل: 034858600 / 034858700\n"
            "📩 لو عايز المنيو أو سعر صنف اكتب اسمه."
        )

    # search products
    match_name, price, unit, suggestions = find_product(msg)

    if match_name:
        # if price present -> normal reply
        if price is not None:
            return format_price_reply(match_name, price, unit)
        else:
            # name known but price missing
            log_unknown_query(user_id, msg)
            return format_price_reply(match_name, None, unit)

    # if we have suggestions (from find_product) return them as short list
    if suggestions:
        sug_lines = "\n".join(f"- {s}" for s in suggestions)
        return f"🔎 ممكن تقصد واحد من دول؟\n{sug_lines}\n\nلو لا، ابعتلي اسم المنتج تاني أو اكتب 'المنيو' عشان أعرض القوائم."

    # no match at all => start by showing menu (as requested), do not start with 'المنتج...غير متاح'
    log_unknown_query(user_id, msg)
    # Try to use OpenAI to produce a nicer suggestion/correction if API key exists
    if OPENAI_API_KEY:
        prompt = (
            "أنت بوت خدمة عملاء لمطعم حلويات. عندنا قائمة منتجات وأسعار (موجودة محليًا).\n"
            f"رسالة العميل: {msg}\n\n"
            "اقترح اختصارًا بسيطًا بالعربية لماذا لم نجد المنتج (مثلاً خطأ إملائي أو اسم مختلف) واقترح أقرب اسماء أو اطلب منه يكتب الاسم بالضبط. "
            "رد مختصر وبلهجة مصرية."
        )
        ai_resp = await call_openai_chat(prompt)
        if ai_resp:
            return ai_resp.strip() + "\n\n" + menu_reply_short()

    # fallback simple reply (menu first)
    return menu_reply_short()

# ---------- send message ----------
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.error("PAGE_TOKEN not set. Cannot send message.")
        return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code not in (200, 201):
            logger.error("Failed to send message: %s %s", r.status_code, r.text)
        else:
            logger.info("📤 Sent to %s | Status: %s", user_id, r.status_code)
    except Exception as e:
        logger.exception("Error sending message: %s", e)

# ---------- FastAPI ----------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "mode": "local-first", "openai_enabled": bool(OPENAI_API_KEY)}

@app.get("/reload-data")
def http_reload_data():
    # endpoint to trigger reload after you update data.txt
    reload_data()
    return {"status": "reloaded", "items": len(all_names)}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info("📩 Incoming Event: %s", body)
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                sender = msg.get("sender", {}).get("id")
                if not sender:
                    continue
                if "message" in msg and "text" in msg["message"]:
                    text = msg["message"]["text"]
                    logger.info("👤 User %s says: %s", sender, text)
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
                # handle postbacks or attachments if needed
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

# ---------- run ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    reload_data()
    uvicorn.run(app, host="0.0.0.0", port=port)