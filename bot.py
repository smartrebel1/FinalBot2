# bot.py
# نسخة كاملة ومحدثة لبوت فيسبوك — ذكي في البحث داخل data/raw_data.json
# ميزات:
# - يقرأ data/raw_data.json المنظم (raw JSON كبير)
# - تطابق ذكي للمنتجات (normalization + fuzzy matching)
# - اقتراح المنيو وروابطه لو المنتج غير معروف
# - أوامر تحكم: stop (يقف الرد على المستخدم حتى يرسل start)
# - يحتفظ بحالة "موقوف" لكل مستخدم في paused_users.json
# - سجل الميموري (memory.txt) لتخزين تحديثات أسعار/FAQs (حسب قواعد الذاكرة)
# - خيار استخدام مزود AI خارجي (OPENAI_API_KEY أو GROQ_API_KEY) لصياغة رد أذكى
# متطلبات: fastapi, uvicorn, requests, httpx, python-dotenv, Unidecode (موصى به)
# استخدم: uvicorn bot:app --host 0.0.0.0 --port $PORT

import os
import json
import re
import time
import logging
from difflib import get_close_matches
from datetime import datetime
from typing import Optional

import requests
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# ----- إعدادات اللوقينج -----
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("bot")

# عرض رسالة تشغيل مع اسم الموديل (قابل للتعديل عبر env)
load_dotenv()
MODEL = os.getenv("MODEL", "local-rules-first")
logger.info(f"🚀 BOT RUNNING WITH MODEL: {MODEL}")

# ----- إعدادات فيسبوك ومفاتيح (env) -----
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "verify_token_here")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ----- مسارات الملفات -----
RAW_JSON_PATH = os.getenv("RAW_JSON_PATH", "data/raw_data.json")
PAUSED_PATH = os.getenv("PAUSED_PATH", "data/paused_users.json")
MEMORY_PATH = os.getenv("MEMORY_PATH", "data/memory.txt")

# ----- تحميل Unidecode إن وُجد لتحسين التطبيع -----
try:
    from unidecode import unidecode
except Exception:
    def unidecode(x):
        return x

# ----- مساعدة: قراءة JSON/raw data -----
def safe_load_json(path: str):
    if not os.path.exists(path):
        logger.warning(f"⚠️ ملف البيانات غير موجود: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

RAW = safe_load_json(RAW_JSON_PATH)

# ----- بناء فهرس للبحث السريع -----
def normalize_ar(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.strip().lower()
    # أحرف عربية متشابهة توحيد
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ة", "ه").replace("ى", "ي")
    # إزالة التشكيل والرموز
    s = re.sub(r"[^\w\s\u0600-\u06FF]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return unidecode(s)

# فهرس: map normalized_alias -> item
INDEX = {}
NAME_TO_ITEM = {}

def build_index():
    global RAW, INDEX, NAME_TO_ITEM
    RAW = safe_load_json(RAW_JSON_PATH) or {}
    INDEX = {}
    NAME_TO_ITEM = {}
    categories = RAW.get("categories", {})
    for cat_name, items in categories.items():
        for it in items:
            # ensure minimal fields exist
            name = it.get("name", "").strip()
            code = it.get("code", "")
            aliases = it.get("aliases") or []
            # add name as alias
            if name and name not in aliases:
                aliases.append(name)
            # generate basic fallback aliases
            norm_aliases = set()
            for a in aliases:
                na = normalize_ar(a)
                if na:
                    norm_aliases.add(na)
                na2 = na.replace(" ", "")
                if na2:
                    norm_aliases.add(na2)
            # add also name variations
            norm_aliases.add(normalize_ar(name))
            # map into index
            for na in norm_aliases:
                INDEX[na] = it
            # store by code/name
            NAME_TO_ITEM[name] = it

build_index()

# ----- إدارة حالة pause للمستخدمين -----
def load_paused():
    if not os.path.exists(PAUSED_PATH):
        return {}
    with open(PAUSED_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_paused(paused):
    os.makedirs(os.path.dirname(PAUSED_PATH) or ".", exist_ok=True)
    with open(PAUSED_PATH, "w", encoding="utf-8") as f:
        json.dump(paused, f, ensure_ascii=False, indent=2)

PAUSED = load_paused()

def set_paused(user_id: str, paused: bool):
    global PAUSED
    if paused:
        PAUSED[user_id] = {"paused_at": datetime.utcnow().isoformat()}
    else:
        if user_id in PAUSED:
            PAUSED.pop(user_id)
    save_paused(PAUSED)

def is_paused(user_id: str) -> bool:
    return str(user_id) in PAUSED

# ----- إدارة الذاكرة (بسيطة) -----
def append_memory(line: str):
    # لا تحفظ بيانات شخصية — استعمل بحذر
    os.makedirs(os.path.dirname(MEMORY_PATH) or ".", exist_ok=True)
    with open(MEMORY_PATH, "a", encoding="utf-8") as f:
        f.write(line.strip() + "\n")

# ----- البحث الذكي عن العنصر -----
def find_item_local(query: str, cutoff: float = 0.6) -> Optional[dict]:
    qn = normalize_ar(query)
    if not qn:
        return None
    # direct
    if qn in INDEX:
        return INDEX[qn]
    # try close matches
    keys = list(INDEX.keys())
    matches = get_close_matches(qn, keys, n=5, cutoff=cutoff)
    if matches:
        return INDEX[matches[0]]
    # try token-based partial match
    tokens = qn.split()
    for t in tokens:
        if t in INDEX:
            return INDEX[t]
    return None

# ----- قوالب الرد -----
def format_item_reply(item: dict) -> str:
    # يصيغ الرد العربي مع إيموجي
    name = item.get("name", "المنتج")
    price = item.get("price")
    unit = item.get("unit") or item.get("measure") or "غير متاح"
    code = item.get("code", "")
    parts = []
    parts.append(f"🧾 **{name}**")
    if price is not None and str(price).strip() != "":
        parts.append(f"💰 السعر: {price:.2f} جنيه")
    else:
        parts.append(f"💰 السعر: غير متاح")
    parts.append(f"📦 الوحدة: {unit}")
    if code:
        parts.append(f"🔢 كود المنتج: {code}")
    # قليل من النص الودي
    parts.append("✅ لو تحب أرسلك طريقه الطلب أو أضغط على الرابط في المنيو.")
    return "\n".join(parts)

def menu_reply_links() -> str:
    meta = RAW.get("metadata", {})
    links = meta.get("menus_links", [])
    lines = ["🍰 تقدر تشوف المنيو الكامل هنا:"]
    for ln in links:
        lines.append(ln)
    lines.append("\n✳️ لو محتاج سعر صنف معين اكتب اسمه بالضبط أو أقرب شكل ليه.")
    lines.append("📩 سيتم التواصل معك لو احتجنا توضيح إضافي.")
    return "\n".join(lines)

# ----- خيار استخدام AI خارجي لصياغة رد ذكي (اختياري) -----
async def call_ai_for_polish(user_msg: str, matched_item: Optional[dict] = None) -> Optional[str]:
    """
    إذا كنت تريد أن تطلب من مزود خارجي صيغ ردود أفضل.
    سيختار تلقائياً OpenAI إذا متوفر، وإلا GROQ لو مُعرف.
    ملاحظة: وضع هذا كخيار — لن يُستخدم إن لم توجد مفاتيح.
    """
    # if no API keys, skip
    if not OPENAI_API_KEY and not GROQ_API_KEY:
        return None

    # نجهز prompt بسيط
    prompt_lines = [
        "أنت مساعد دردشة لصفحة حلويات مصر. يجب أن ترد بالعربية وبلهجة مصرية مهذبة ومختصرة.",
        "استخدم فقط المعلومات المتوفرة من data/raw_data.json إن وُجدت.",
        f"رسالة العميل: {user_msg}"
    ]
    if matched_item:
        prompt_lines.append("المنتج المطابق:")
        prompt_lines.append(json.dumps({
            "name": matched_item.get("name"),
            "price": matched_item.get("price"),
            "unit": matched_item.get("unit"),
            "code": matched_item.get("code")
        }, ensure_ascii=False))
    prompt = "\n".join(prompt_lines)

    # Use OpenAI ChatCompletions if key provided
    if OPENAI_API_KEY:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
            payload = {
                "model": "gpt-4o-mini",  # مثال — المستخدم يمكنه تغييره عبر env
                "messages": [{"role":"system","content":"You are a helpful assistant."},
                             {"role":"user","content":prompt}],
                "temperature": 0.2,
                "max_tokens": 300
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    return content
                else:
                    logger.error(f"OpenAI error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"OpenAI call failed: {e}")

    # Groq (example) if provided
    if GROQ_API_KEY:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
            payload = {
                "model": os.getenv("GROQ_MODEL", "mixtral-8x7b-32768"),
                "messages": [{"role":"user","content":prompt}],
                "temperature": 0.2
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    return content
                else:
                    logger.error(f"Groq error: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"Groq call failed: {e}")

    return None

# ----- FastAPI app و routes -----
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "model": MODEL}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Forbidden")

# helper: send message to Facebook
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.warning("⚠️ PAGE_TOKEN غير معرف — لن يتم ارسال الرسائل فعليًا.")
        return None
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, json=payload, timeout=8)
        logger.info(f"📤 Sent to {user_id}: {text[:80]} | Status: {r.status_code}")
        return r.status_code
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return None

# الرد الذكي الرئيسي
async def generate_reply(user_id: str, user_msg: str) -> str:
    """
    منطق الرد:
    1) إذا user paused -> إذا الرسالة هي 'start' أو مرادف -> resume
       إذا الرسالة هي 'stop' أو مرادف -> pause
    2) بحث محلي ذكي في data raw
       - لو وجد عنصر -> رد بصيغة price/unit (format_item_reply)
       - لو ما وجد -> أرسل روابط المنيو مع نص متابعة
    3) إن توافر API خارجي وطلبنا تلميع الرد -> call_ai_for_polish
    """
    # commands (stop/start)
    q_low = user_msg.strip().lower()
    if q_low in ["stop", "قف", "بس", "كفى"]:
        set_paused(user_id, True)
        return "⛔ تم إيقاف الردود لك. اكتب `start` أو `ابدأ` علشان أكمل الرد تاني."
    if q_low in ["start", "ابدأ", "كمل"]:
        set_paused(user_id, False)
        return "✅ تمام — رجعت تاني، ممكن أساعدك بإيه؟"

    # if user is paused -> ignore except start
    if is_paused(user_id):
        return "⛔ أنت حاليا مُوقّف. اكتب `start` أو `ابدأ` لو عايز أرجع أرد."

    # 1) بحث محلي ذكي
    item = find_item_local(user_msg, cutoff=0.6)
    if item:
        # لو السعر في item مختلف عن الذاكرة — إمكانية حفظ تحديث (مثال)
        # صياغة الرد
        local_reply = format_item_reply(item)
        # حاول تحسين الصياغة عبر AI إن مفعل
        ai_polished = await call_ai_for_polish(user_msg, matched_item=item)
        if ai_polished:
            return ai_polished
        return local_reply

    # 2) لم نجد المنتج — نعرض المنيو والروابط أولًا (وفق طلبك)
    menu_text = menu_reply_links()
    # نحاول اقتراح أقرب تطابقات (fuzzy) لافتراضات المستخدم
    qn = normalize_ar(user_msg)
    # نبحث عن أقرب مفاتيح من INDEX
    keys = list(INDEX.keys())
    close = get_close_matches(qn, keys, n=3, cutoff=0.5)
    suggestion = ""
    if close:
        suggested_item = INDEX[close[0]]
        suggestion = f"\n🔎 أقرب نتيجة ممكن تقصد: {suggested_item.get('name')}\nلو ده اللي تقصده اكتب اسمه بالشكل ده بالضبط."
    reply = f"{menu_text}{suggestion}\n\n📩 سيتم التواصل معك في أقرب وقت لو احتجنا تفاصيل."
    # سجل في الميموري أن سؤالاً متكرراً لم يجد تطابق (بصيغة عامة، بدون بيانات شخصية)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d")
    append_memory(f"{timestamp} — FAQ_MISS — \"عميل سأل عن: {user_msg[:120]}\"")
    return reply

# webhook handler
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        # iterate events
        for entry in body.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender = messaging.get("sender", {}).get("id")
                # message text
                if messaging.get("message") and "text" in messaging["message"]:
                    text = messaging["message"]["text"]
                    logger.info(f"👤 User {sender} says: {text}")
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
                # optionally: postbacks, attachments handling can be added here
        return JSONResponse({"status": "ok"}, status_code=200)

    return JSONResponse({"status": "ignored"}, status_code=200)

# ----- أداة مساعدة: إعادة بناء الفهرس عند تحديث data/raw_data.json ----- 
@app.post("/admin/reload-data")
def admin_reload_data(secret: Optional[str] = None):
    # حماية بسيطة: استخدم env ADMIN_SECRET إن رغبت
    ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
    if ADMIN_SECRET and secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    build_index()
    return {"status": "reloaded", "items_indexed": len(INDEX)}

# ----- نقطة صحية بسيطة -----
@app.get("/health")
def health():
    return {"ok": True, "data_loaded": bool(RAW)}

# ----- تشغيل محلي -----
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("bot:app", host="0.0.0.0", port=port, reload=False)
