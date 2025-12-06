# bot.py
import os
import json
import logging
import time
import re
from pathlib import Path
from difflib import get_close_matches
from typing import Dict, Tuple, Optional

import requests
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# ---------- إعدادات لوجنج ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bot")
logger.info("🚀 BOT RUNNING WITH ChatGPT BACKEND (Local-first with optional OpenAI)")

# ---------- تحميل متغيرات البيئة ----------
load_dotenv()
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)  # optional: لو مش موجود، يستخدم الردود المحلية

# ---------- ملفات البيانات ----------
DATA_FILE = Path("data.txt")
MEMORY_FILE = Path("memory.json")

# روابط المنيو (لو تحب تعدلها في data.txt ممكن، لكن هنا كـ fallback)
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

# ---------- مساعدة: قراءة وتهيئة الذاكرة ----------
def ensure_memory() -> Dict:
    if not MEMORY_FILE.exists():
        logger.info("إنشاء ملف memory.json جديد")
        empty = {"paused_users": {}, "unknown_queries": []}
        MEMORY_FILE.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        # ensure keys
        if "paused_users" not in data:
            data["paused_users"] = {}
        if "unknown_queries" not in data:
            data["unknown_queries"] = []
        return data
    except Exception as e:
        logger.error("خطأ في قراءة memory.json، سيتم إعادة إنشاءه: %s", e)
        empty = {"paused_users": {}, "unknown_queries": []}
        MEMORY_FILE.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty

def save_memory(mem: Dict):
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")

memory = ensure_memory()

# ---------- قراءة Data.txt وفهرستها ----------
def parse_line_for_item(line: str) -> Optional[Tuple[str, float, str]]:
    """
    يحاول استخراج الاسم والسعر والوحدة من سطر نصي
    تقبل صيغ مختلفة: 'اسم: 130 — KG' أو 'اسم\t...\t130.00\tKG'
    """
    line = line.strip()
    if not line:
        return None

    # استبعاد رؤوس أو أرقام فقط
    if re.fullmatch(r"[\d\-\t,\. ]+", line):
        return None

    # نجرب فصل على '—' '—' '-' ':' '—' أو '—' ascii em dash
    # نبحث عن آخر رقم في السطر كالسعر
    # pattern بسيط للبحث عن السعر (رقم يحتوي على فاصلة عشرية اختيارية)
    price_match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:$|\b)", line)
    if price_match:
        price_str = price_match.group(1).replace(",", ".")
        try:
            price = float(price_str)
        except:
            price = None
    else:
        price = None

    # الوحدة: كلمة بعد السعر أو وجود 'KG' أو 'Unit' أو 'كجم' الخ.
    unit = None
    unit_match = re.search(r"(KG|Unit|كجم|ك|كيلو|جم|جرام|Unit|Unit )", line, re.IGNORECASE)
    if unit_match:
        unit = unit_match.group(1)

    # اسم المنتج: نأخذ بداية السطر إلى قبل السعر إن أمكن، وإزالة أكواد
    # نحاول إزالة أرقام/كود من البداية
    # split by common separators and pick the chunk that looks like name (non-numeric)
    # first remove tabs and many spaces
    parts = re.split(r"\t+|\s{2,}|\s-\s|\s—\s|:|–|-", line)
    # keep the longest part containing letters (arabic)
    candidate = None
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # ignore if p is mostly digits or codes
        if re.search(r"[A-Za-z\u0600-\u06FF]", p):  # contains arabic or letters
            if candidate is None or len(p) > len(candidate):
                candidate = p
    name = candidate or line

    # cleanup name from price fragment if still present
    name = re.sub(r"\d[\d\.,]*", "", name).strip(" -,:؛؛\t")

    return (name, price if price is not None else None, unit or "")

def load_data() -> Dict[str, Dict]:
    """
    يعيد dict: name -> {"price": float|None, "unit": str}
    """
    index = {}
    if not DATA_FILE.exists():
        logger.warning("data.txt غير موجود — الفهرس فارغ")
        return index

    text = DATA_FILE.read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in lines:
        parsed = parse_line_for_item(line)
        if parsed:
            name, price, unit = parsed
            key = name.strip()
            if key:
                # لو نفس الاسم موجود، لا تحذفه — حافظ على أول قيمة أو حدثها
                index[key] = {"price": price, "unit": unit}
    logger.info("تم تحميل %d صنف من data.txt", len(index))
    return index

data_index = load_data()
all_names = list(data_index.keys())

# ---------- أدوات المساعدة للبحث ----------
def find_product(name: str) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[list]]:
    """
    يحاول إيجاد تطابق مباشر، أو تطابق قريب (fuzzy).
    يعيد: match_name, price, unit, suggestions
    """
    name = name.strip()
    # direct exact (case-insensitive)
    for k in all_names:
        if k.strip().lower() == name.lower():
            info = data_index.get(k)
            return k, info.get("price"), info.get("unit"), []

    # substring match (contains)
    substr_matches = [k for k in all_names if name.lower() in k.lower()]
    if substr_matches:
        chosen = substr_matches[0]
        info = data_index.get(chosen)
        suggestions = substr_matches[:5]
        return chosen, info.get("price"), info.get("unit"), suggestions

    # fuzzy using difflib
    close = get_close_matches(name, all_names, n=5, cutoff=0.6)
    if close:
        chosen = close[0]
        info = data_index.get(chosen)
        return chosen, info.get("price"), info.get("unit"), close

    # no match
    return None, None, None, []

# ---------- توليد الرد المحلي (بدون AI خارجي) ----------
def format_price_reply(name: str, price: Optional[float], unit: Optional[str]) -> str:
    if price is None:
        # رسالة افتراضية عند عدم توفر السعر
        menu_text = "\n".join(MENU_LINKS)
        return (
            f"🧾 **{name}**\n"
            f"💰 السعر: غير متاح\n"
            f"📦 الوحدة: غير متاح\n\n"
            f"❗ المنتج اللي بتدور عليه غير موجود بالسعر عندنا دلوقتي.\n"
            f"📋 تقدر تشوف المنيو الكامل هنا:\n{menu_text}\n\n"
            "📩 سيتم التواصل معك في أقرب وقت للتأكيد. 😊"
        )
    else:
        # price present
        # format price (no trailing .0)
        price_str = str(int(price)) if price == int(price) else f"{price:.2f}"
        unit_str = unit or "وحدة"
        return f"✅ **{name}**\n💰 السعر: {price_str} ج\n📦 الوحدة: {unit_str}\nلو حابب أضيفه للطلب اكتب: طلب {name} ✅"

# ---------- استدعاء ChatGPT (اختياري) ----------
async def call_openai_chat(prompt: str) -> Optional[str]:
    if not OPENAI_API_KEY:
        return None
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",  # اختياري: تقدر تغيره للموديل اللي عندك صلاحية له
        "messages": [{"role": "system", "content": "أنت مساعد خدمة عملاء عربي مصري."},
                     {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    # طلب مع retry بسيط
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                data = r.json()
                txt = data["choices"][0]["message"]["content"].strip()
                return txt
            else:
                logger.error("OpenAI returned %s: %s", r.status_code, r.text)
        except Exception as e:
            logger.error("OpenAI call error attempt %d: %s", attempt + 1, e)
        time.sleep(1)
    return None

# ---------- أذونات التشغيل (pause / resume) ----------
def is_paused(user_id: str) -> bool:
    mem = ensure_memory()
    return mem.get("paused_users", {}).get(str(user_id), False)

def set_paused(user_id: str, value: bool):
    mem = ensure_memory()
    mem["paused_users"][str(user_id)] = bool(value)
    save_memory(mem)

def log_unknown_query(user_id: str, text: str):
    mem = ensure_memory()
    mem.setdefault("unknown_queries", []).append({"user": str(user_id), "text": text, "ts": int(time.time())})
    save_memory(mem)

# ---------- وظيفة توليد الرد النهائي (المنطق الرئيسي) ----------
async def generate_reply(user_id: str, user_msg: str) -> str:
    # تنظيف الرسالة
    msg = user_msg.strip()

    # أوامر تحكم سريعة
    cmd_stop = ["stop", "قف", "وقف", "stopً", " توقف"]
    cmd_start = ["start", "ابدأ", "استأنف", "استئناف", "resume"]

    low = msg.lower()
    if any(low == c for c in cmd_stop):
        set_paused(user_id, True)
        return "⛔ تم إيقاف الردود مؤقتًا. اكتب 'ابدأ' أو 'start' عشان يرجع يرد تاني."

    if any(low == c for c in cmd_start):
        set_paused(user_id, False)
        return "✅ جاهز تاني! أنا رجعت وباقي أساعدك 👋"

    # لو المستخدم متوقف (pause)، لا نرد بمحتوى عادي
    if is_paused(user_id):
        return "🔕 البوت متوقف مؤقتاً عندك. لو عايز رجوع اكتب 'ابدأ'."

    # لو الرسالة طلب القوائم أو كلمة "المنيو" أو "منيو" إلخ -> نعرض الروابط مباشرة
    if re.search(r"\b(المنيو|منيو|قائمة|قائمة|menu)\b", msg, re.IGNORECASE):
        menu_text = "\n".join(MENU_LINKS)
        return f"📋 تقدر تشوف المنيو الكامل هنا:\n{menu_text}\n\nلو حبيت أعرف سعر منتج معين اكتب اسمه وانا أقولك السعر."

    # لو الرسالة تطلب "مواعيد" أو "فروع" -> نرد برد جاهز (تقدر تعدّل المحتوى ده في data.txt أو هنا)
    if re.search(r"\b(مواعيد|ساعات|فروع|فرع|تليفون|رقم)\b", msg, re.IGNORECASE):
        return (
            "🕒 **مواعيد العمل**\n"
            "جميع الأيام: من 8 صباحًا إلى 10 مساءً\n"
            "الخميس والجمعة: حتى 11 مساءً\n\n"
            "🏬 **فروع**\n"
            "طنطا - ميدان الساعة: 0403335941 / 0403335942\n"
            "الإسكندرية - محطة الرمل: 034858600 / 034858700\n\n"
            "لو عايز منيو أو سعر صنف اكتب اسم المنتج."
        )

    # نجرّب البحث في ال Data
    match_name, price, unit, suggestions = find_product(msg)
    if match_name:
        # لو فيه سعر نرد بالسعر مباشرة
        if price is not None:
            reply = format_price_reply(match_name, price, unit)
            return reply
        else:
            # لو الاسم موجود لكن السعر مفقود
            # نقترح المنيو ونسجل الاستفسار كـ unknown
            log_unknown_query(user_id, msg)
            menu_text = "\n".join(MENU_LINKS)
            return (
                f"🧾 **{match_name}**\n"
                "💰 السعر: غير متاح حالياً\n\n"
                f"📋 المنيو الكامل هنا:\n{menu_text}\n\n"
                "📩 سيتم التواصل معك للتأكيد في أقرب وقت."
            )

    # لو فيه اقتراحات (matches) — نعرض اقتراحات
    if suggestions:
        sug_text = "\n".join(f"- {s}" for s in suggestions)
        return (
            "🔎 ممكن تقصد أحد المنتجات دي؟\n"
            f"{sug_text}\n\n"
            "لو لا، ابعتلي الاسم تاني أو اكتب 'المنيو' عشان أعرض لك القائمة كاملة."
        )

    # لو مالوش أي تطابق — نسجل ونعرض المنيو + نعرض خيار تواصل لاحق
    log_unknown_query(user_id, msg)
    menu_text = "\n".join(MENU_LINKS)

    # احاول استدعاء OpenAI لو موجود API_KEY لصياغة رد أذكى (مثلاً لتصحيح خطأ إملائي أو تقديم اقتراح)
    if OPENAI_API_KEY:
        prompt = (
            "أنت بوت خدمة عملاء لمطعم حلويات. عندنا المعلومات التالية (أسماء منتجات وأسعار) - "
            "أجب بالعربية بطلاقة واقترح أقرب منتجات ممكن العميل يقصدها إذا سأل بشيء غير واضح.\n\n"
            f"DATA_KEYS: {', '.join(all_names[:60])}...\n\n"  # لا نضيف كل الأسماء الكبيرة لتقليل الطول
            f"رسالة العميل: {msg}\n\n"
            "رد باقتراحات قصيرة وفصيحة وبلهجة مصرية. لو مش عارف قل: 'المعلومة دي مش موجودة عندي حالياً.' "
            "وفي حالة عدم المعرفة، أعرض روابط المنيو التالية ثم قل 'سيتم التواصل معك في أقرب وقت.'"
        )
        ai_resp = await call_openai_chat(prompt)
        if ai_resp:
            # نلحق الروابط في النهاية لضمان ظهورها
            return ai_resp + "\n\n📋 المنيو الكامل هنا:\n" + menu_text + "\n\n📩 سيتم التواصل معك في أقرب وقت."
        else:
            # لو فشل OpenAI، نرجع الرد المحلي
            return (
                "❗ المنتج اللي بتدور عليه غير موجود بالقائمة الحالية.\n\n"
                f"📋 تقدر تشوف المنيو الكامل هنا:\n{menu_text}\n\n📩 سيتم التواصل معك في أقرب وقت."
            )

    # لو مفيش OpenAI نرد فوراً بالمنيو
    return (
        "❗ المنتج اللي بتدور عليه غير موجود بالقائمة الحالية.\n\n"
        f"📋 تقدر تشوف المنيو الكامل هنا:\n{menu_text}\n\n📩 سيتم التواصل معك في أقرب وقت."
    )

# ---------- إرسال رسالة للفيسبوك ----------
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.error("PAGE_TOKEN غير مضبوط — لا يمكن إرسال رسالة.")
        return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code not in (200, 201):
            logger.error("فشل إرسال الرسالة: %s %s", r.status_code, r.text)
        else:
            logger.info("📤 Sent to %s | Status: %s", user_id, r.status_code)
    except Exception as e:
        logger.exception("خطأ عند إرسال رسالة: %s", e)

# ---------- FASTAPI endpoints ----------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "mode": "local-first", "openai": bool(OPENAI_API_KEY)}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch")

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info("📩 Incoming Event: %s", body)
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                sender = msg.get("sender", {}).get("id")
                # Handle text message
                if "message" in msg and "text" in msg["message"]:
                    text = msg["message"]["text"]
                    logger.info("👤 User %s says: %s", sender, text)
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
                # optionally handle postback, attachments, etc.
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

# ---------- تشغيل محلي ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)