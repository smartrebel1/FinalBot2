# bot.py
import os
import re
import time
import json
import logging
import requests
from difflib import get_close_matches
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("bot")
logger.info("🚀 BOT RUNNING WITH LLAMA-3.3-70B-VERSATILE (GROQ)")

# ---------- load env safely ----------
from dotenv import load_dotenv
try:
    load_dotenv()
except Exception:
    # if python-dotenv missing or .env absent, continue gracefully
    logger.debug("dotenv not loaded or not available - continuing")

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN") or os.getenv("VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or os.getenv("PAGE_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.getenv("PORT", 8080))

# ---------- constants ----------
DATA_FILE = "data.txt"
MEMORY_FILE = "memory.txt"
PAUSE_FILE = "paused.json"  # keep paused users
MENU_TEXT = (
    "📋 تقدر تشوف المنيو هنا:\n"
    "منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5\n"
    "منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8\n"
    "منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5\n"
    "منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5\n"
    "منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA\n"
    "منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8\n"
    "منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8\n"
    "جميع الكتالوجات: https://misrsweets.com/catalogs/\n"
)

# ---------- helper utils ----------
def safe_read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read {path}: {e}")
        return ""

def safe_write(path: str, text: str):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        logger.error(f"Failed to write {path}: {e}")

def load_paused():
    if os.path.exists(PAUSE_FILE):
        try:
            return json.loads(safe_read(PAUSE_FILE)) or {}
        except Exception:
            return {}
    return {}

def save_paused(d):
    safe_write(PAUSE_FILE, json.dumps(d, ensure_ascii=False, indent=2))

PAUSED = load_paused()  # dict of user_id -> {"paused":True, "since":timestamp}

# ---------- data parsing / search ----------
def parse_price_from_lines(lines, index):
    """Try to find a number/price on the same line or following lines near index."""
    # search current line and up to next 3 lines
    for i in range(index, min(len(lines), index + 4)):
        line = lines[i]
        # look for numbers like 1,435.00 or 1435.00 or 1350
        m = re.search(r"(\d{1,3}(?:[,\d]{0,})?(?:\.\d{1,2})?)", line.replace("٬", "").replace("٫", "."))
        if m:
            price = m.group(1).replace(",", "")
            # try detect unit (KG, Unit, قطعة, ك)
            unit = None
            if re.search(r"\bKG\b|\bKg\b|\bك\b|كجم|كيلو", line, re.IGNORECASE):
                unit = "KG"
            elif re.search(r"\bUnit\b|\bقطعة\b|\bقط\b|\bوحدة\b", line, re.IGNORECASE):
                unit = "Unit"
            else:
                # try to find unit on next tokens
                pass
            return price, unit
    return None, None

def build_search_index(data_text: str):
    """
    Build a simple index: list of product names and their context lines for fuzzy match.
    We'll split data_text into lines and keep them.
    """
    lines = [l.strip() for l in data_text.splitlines() if l.strip()]
    # product_names: collect tokens before numeric price, or whole short lines
    products = []
    for i, line in enumerate(lines):
        # if line contains Arabic letters and also a number -> could be product+price; keep product part
        if re.search(r"[0-9]\s*$", line):  # line ends with a number
            # split by double spaces or tabs
            p = re.split(r"\s{2,}|\t|\|", line)[0].strip()
            if p:
                products.append((p, i, line))
        else:
            # if line short (<40) and contains letters, consider as candidate name
            if len(line) < 80 and re.search(r"[ء-يA-Za-z]", line):
                products.append((line, i, line))
    # also include entire data_text as fallback
    return lines, products

def find_best_match(query: str, products, n=5):
    # extract only names
    names = [p[0] for p in products]
    query_norm = query.strip().lower()
    # exact substring match first
    matches = []
    for name, idx, raw in products:
        if query_norm in name.lower():
            matches.append((name, idx, raw))
    if matches:
        return matches
    # fuzzy using difflib
    close = get_close_matches(query_norm, names, n=n, cutoff=0.6)
    results = []
    for c in close:
        for name, idx, raw in products:
            if name == c:
                results.append((name, idx, raw))
                break
    return results

# ---------- AI / LLM call (optional) ----------
async def call_groq(prompt: str, model: str = "mixtral-8x7b-32768", retries: int = 2, timeout: int = 15):
    """
    Non-blocking call to Groq/OpenAI-style endpoint.
    If GROQ_API_KEY missing, return None gracefully.
    """
    if not GROQ_API_KEY:
        logger.debug("GROQ_API_KEY not set - skipping LLM call")
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 400
    }
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code == 200:
                    j = r.json()
                    return j.get("choices", [{}])[0].get("message", {}).get("content")
                else:
                    logger.warning(f"LLM call failed {r.status_code}: {r.text}")
        except Exception as e:
            logger.error(f"LLM call exception: {e}")
        time.sleep(1)
    return None

# ---------- response builder ----------
def format_price_reply(name: str, price: Optional[str], unit: Optional[str]) -> str:
    # Arabic concise reply with emojis
    emoji_price = "💰"
    emoji_unit = "📦"
    emoji_ask = "✍️"
    if price:
        unit_display = unit if unit else "Unit"
        return f"✅ *{name}*\n{emoji_price} السعر: {price} جنيه\n{emoji_unit} الوحدة: {unit_display}\n\n{emoji_ask} لو تحب تطلب اكتب \"طلب\" أو اكتب الكمية."
    else:
        # price unknown: show menu + fallback message
        return (
            f"📋 *{name}*\n"
            f"💰 السعر: غير متاح حالياً\n"
            f"📦 الوحدة: غير متاح\n\n"
            f"❗ ممكن تشوف المنيو الكامل هنا:\n{MENU_TEXT}\n"
            f"📩 سيتم التواصل معك في أقرب وقت."
        )

def unknown_product_reply():
    # when bot doesn't know, send full menu links and friendly text
    return (
        f"معلش مش لاقي المنتج ده عندي بشكل مباشر 😕\n\n"
        f"{MENU_TEXT}\n"
        f"✳️ لو محتاج سعر صنف معين اكتب اسمه بالضبط أو أقرب شكل ليه.\n"
        f"📩 هيتم التواصل معك لو محتاجين توضيح."
    )

# ---------- main generate reply ----------
async def generate_reply(user_msg: str, user_id: Optional[str] = None) -> str:
    """
    Main logic:
    - handle user commands: stop, resume
    - search data.txt for product
    - if exact/close match found: return price reply
    - else: return menu links + will contact
    """
    text = user_msg.strip()
    if not text:
        return "لو سمحت اكتب سؤالك 😊"

    # commands (in Arabic + English)
    cmd_stop = ["stop", "توقف", "سكت", "اوقف", "وقف", "pause"]
    cmd_resume = ["resume", "ابدأ", "استمر", "تابع", "resume", "استئنف", "كمل"]

    lower = text.lower()
    # stop command
    if any(lower == c or lower.startswith(c + " ") for c in cmd_stop):
        if user_id:
            PAUSED[str(user_id)] = {"paused": True, "since": int(time.time())}
            save_paused(PAUSED)
            logger.info(f"Paused user {user_id}")
        return "🛑 تم إيقاف الردود عليك مؤقتًا. اكتب `resume` أو `استمر` لما تحب أرجع أتكلم معاك."

    if any(lower == c or lower.startswith(c + " ") for c in cmd_resume):
        if user_id and str(user_id) in PAUSED:
            PAUSED.pop(str(user_id), None)
            save_paused(PAUSED)
            logger.info(f"Resumed user {user_id}")
        return "✅ تم استئناف الردود. أنا جاهز تاني ✨"

    # if user paused, return nothing (or an acknowledgement)
    if user_id and str(user_id) in PAUSED:
        return "🛑 البوت في وضع سكون عندك — اكتب `resume` لو حابب أرجع أتعامل مع رسائل حضرتك."

    # load data
    data_text = safe_read(DATA_FILE)
    if not data_text:
        # fallback: send menu links
        return (
            "المعذرة — ملف الأسعار غير موجود حالياً عندي.\n\n"
            f"{MENU_TEXT}\n"
            "📩 سيتم التواصل معك في أقرب وقت."
        )

    lines, products = build_search_index(data_text)

    # first try: explicit price pattern like "اسم: 130 — KG" or "اسم: 130 — Unit"
    # quick direct search: look for exact substring
    q = text.strip()
    # normalize spaces
    q_norm = re.sub(r"\s+", " ", q)
    # try to find any product line containing q_norm
    found = None
    for i, line in enumerate(lines):
        if q_norm.lower() in line.lower():
            # parse price near this line
            price, unit = parse_price_from_lines(lines, i)
            # derive product name from line before the price digit
            # fallback product name = the line or nearest preceding short text line
            name = line
            found = (name, price, unit)
            break

    # if not found, try fuzzy match against products list
    if not found:
        matches = find_best_match(q, products, n=5)
        if matches:
            # pick best (first)
            name, idx, raw = matches[0]
            price, unit = parse_price_from_lines(lines, idx)
            found = (name, price, unit)
            # if price missing, try further lines around idx
            if not price:
                price, unit = parse_price_from_lines(lines, max(0, idx-1))
                found = (name, price, unit)

    # if still not found: build suggestion list of close matches to propose
    if not found:
        # prepare suggestions (top 3 fuzzy matches)
        names_only = [p[0] for p in products]
        close = get_close_matches(q_norm.lower(), [n.lower() for n in names_only], n=3, cutoff=0.55)
        suggestion_lines = ""
        if close:
            suggestion_lines = "🔎 ممكن تقصِد:\n" + "\n".join(f"- {s}" for s in close) + "\n\n"
        # return menu + suggestions
        return suggestion_lines + unknown_product_reply()

    # format reply
    name, price, unit = found
    # make name friendly
    name_display = name.split("  ")[0].strip()
    reply = format_price_reply(name_display, price, unit)
    return reply

# ---------- send message ----------
def send_message(user_id: str, text: str):
    try:
        if not PAGE_TOKEN:
            logger.warning("PAGE_TOKEN not set — skipping sending message")
            return
        url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
        payload = {
            "recipient": {"id": user_id},
            "message": {"text": text}
        }
        r = requests.post(url, json=payload, timeout=10)
        logger.info(f"📤 Sent (to {user_id}): {text[:60]} | Status: {r.status_code}")
        if r.status_code != 200:
            logger.warning(f"Send message response: {r.status_code} {r.text}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

# ---------- FastAPI app ----------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "note": "FinalBot active"}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token and challenge and token == VERIFY_TOKEN:
        return int(challenge)
    # friendly message for health checks
    return JSONResponse({"status": "webhook endpoint active"}, status_code=200)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    logger.info(f"📩 Incoming Event: {body}")

    # typical Facebook page webhook structure
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender = messaging.get("sender", {}).get("id")
                # ignore delivery/read events
                if "message" in messaging and isinstance(messaging["message"], dict):
                    text = messaging["message"].get("text") or ""
                    if not text:
                        # ignore attachments for now
                        continue
                    # generate reply
                    try:
                        reply = await generate_reply(text, user_id=sender)
                    except Exception as e:
                        logger.error(f"Error generating reply: {e}")
                        reply = "عذرًا فيه مشكلة دلوقتي في النظام. حاول تاني بعد شويّة."
                    # if reply not empty and user not paused send
                    if reply:
                        send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

# ---------- CLI helper to add memory entry (not automatic training) ----------
def add_memory_entry(entry_type: str, content: str):
    # basic memory append following user's MEMORY_RULES format
    safe = f"{time.strftime('%Y-%m-%d')} — {entry_type} — {content}"
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(safe + "\n")
        logger.info(f"Memory appended: {safe}")
    except Exception as e:
        logger.error(f"Failed to append memory: {e}")

# ---------- run in uvicorn if executed directly ----------
if __name__ == "__main__":
    # quick check: ensure data file exists (but don't crash)
    if not os.path.exists(DATA_FILE):
        logger.warning(f"{DATA_FILE} not found. Create it with product: price — UNIT lines for best results.")
    # ensure paused file exists
    if not os.path.exists(PAUSE_FILE):
        save_paused(PAUSED)
    import uvicorn as _uv
    _uv.run("bot:app", host="0.0.0.0", port=PORT)