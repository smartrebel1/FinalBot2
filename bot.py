# bot.py
# بوت فيسبوك بسيط مع دعم data.txt و memory.txt و fuzzy search
# متطلبات: fastapi, uvicorn, python-dotenv, httpx, requests
# استعمل environment variables:
#   FACEBOOK_VERIFY_TOKEN
#   FACEBOOK_PAGE_ACCESS_TOKEN
#   OPENAI_API_KEY (اختياري — لتحسين الصياغة عن طريق ChatGPT)

import os
import time
import logging
import json
import re
import difflib
from typing import Dict, Tuple, Optional, List
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import requests
import httpx
import uvicorn

load_dotenv()

# -------------------------
# إعداد اللوج
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# -------------------------
# ثوابت و متغيرات
# -------------------------
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # اختياري لتحسين الصياغة
PORT = int(os.getenv("PORT", 8080))

# الوقت الذي لا نكرر فيه ارسال المنيو لنفس المستخدم (بالثواني)
MENU_CONFIRM_WINDOW = 10 * 60  # 10 دقائق

# عتبة التشابه للفوز بالمطابقة
SIMILARITY_THRESHOLD = 0.62  # 62%

# ملفات البيانات
DATA_FILE = "data.txt"
MEMORY_FILE = "memory.txt"

# روابط المنيو (ثابتة — يمكن تعديل)
MENU_LINKS_TEXT = (
    "منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5\n"
    "منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8\n"
    "منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5\n"
    "منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5\n"
    "منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA\n"
    "منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8\n"
    "منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8\n"
    "جميع الكتالوجات: https://misrsweets.com/catalogs/"
)

# كلمات دالة
PRICE_KEYWORDS = ["سعر", "بكام", "كام", "ثمن", "تكلفة", "بكم", "كم", "كام سعر", "عايز سعر"]
MENU_KEYWORDS = ["منيو", "قائمة", "المنيو", "menu", "قائمه"]
CONFIRM_POSITIVE = {"نعم", "ايوه", "أيوه", "عايز", "ابعت", "ابعث", "y", "yes"}
CONFIRM_NEGATIVE = {"لأ", "لا", "مش", "مش عايز", "no"}

# حالات مؤقتة في الذاكرة (تشغيل/ايقاف/منيو حديث الإرسال)
paused_users: Dict[str, float] = {}  # user_id -> timestamp of pause
recent_menu_sent: Dict[str, float] = {}  # user_id -> timestamp last sent menu

# هيكل بيانات الproducts بعد تحميل data.txt
# data_index = { "category": { "item_name": {"price": float_or_str, "unit": "KG/Unit", "raw": "..." } } }
data_index: Dict[str, Dict[str, Dict]] = {}

app = FastAPI()


# -------------------------
# مساعدة: قراءة وكتابة memory.txt
# -------------------------
def load_memory() -> List[str]:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    except Exception as e:
        logger.error("Failed loading memory: %s", e)
        return []


def append_memory(line: str):
    # يحترم قواعد الذاكرة حسب الملف اللي بعتته — هنا نخزن أسطر بصيغة DATE — TYPE — CONTENT
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(line.strip() + "\n")
    except Exception as e:
        logger.error("Failed append memory: %s", e)


# -------------------------
# تحميل data.txt و بناء فهرس مبسط
# يدعم صيغتين شائعتين:
# 1) "اسم المنتج: 130 — KG" (شائع)
# 2) أسطر مفصولة بتبويب أو مسافات تحتوي اسم - سعر - وحدة
# -------------------------
def parse_price(text: str) -> Optional[Tuple[str, str]]:
    # يحاول استخراج السعر والوحدة من نص مثل "130 — KG" أو "130.00 — Unit"
    # يعيد (price_str, unit) أو None
    # نظّف النص
    t = text.replace(",", "").strip()
    # بحث عن رقم
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:[:\-–—]\s*)?([A-Za-z\u0600-\u06FF%]*)$", t)
    if m:
        price = m.group(1)
        unit = m.group(2).strip() or "Unit"
        return price, unit
    # بديل: إذا الحقل كله رقم
    m2 = re.search(r"^(\d+(?:\.\d+)?)$", t)
    if m2:
        return m2.group(1), "Unit"
    return None


def load_data_file():
    global data_index
    data_index = {}
    if not os.path.exists(DATA_FILE):
        logger.warning("data.txt not found — empty index.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        lines = [ln.rstrip() for ln in f.readlines()]

    current_category = "عام"
    for ln in lines:
        if not ln or ln.strip() == "":
            continue
        # محاولة: لو السطر يشبه "Category\tcode\tname\tUnit\tprice"
        if "\t" in ln:
            parts = [p.strip() for p in ln.split("\t") if p.strip()]
            # نبحث عن وجود سعر في آخر جزء
            if len(parts) >= 2:
                # ابحث عن سعر في آخر أجزاء السطر
                last = parts[-1]
                parsed = parse_price(last)
                name = parts[1] if len(parts) > 1 else parts[0]
                if parsed:
                    price, unit = parsed
                    cat = current_category
                    data_index.setdefault(cat, {})[name] = {"price": price, "unit": unit, "raw": ln}
                    continue
            # خلاف ذلك اعتبر السطر اسم فئة
            if len(parts) == 1:
                current_category = parts[0]
                continue

        # لو السطر يحتوي ":" نفترض الصيغة "name: price — unit"
        if ":" in ln:
            left, right = ln.split(":", 1)
            name = left.strip()
            parsed = parse_price(right)
            if parsed:
                price, unit = parsed
                data_index.setdefault(current_category, {})[name] = {"price": price, "unit": unit, "raw": ln}
                continue
            else:
                # لو ما فيه سعر واضح، اعتبر هذا سطر اسم فقط (قد يكون عنوان مجموعة)
                current_category = name
                continue

        # لو السطر صيغة بسيطة "name 130 KG" نجرب استخراج الأرقام
        m = re.search(r"(.+?)\s+(\d+(?:\.\d+)?)\s*([A-Za-z\u0600-\u06FF%]*)$", ln)
        if m:
            name = m.group(1).strip()
            price = m.group(2)
            unit = m.group(3).strip() or "Unit"
            data_index.setdefault(current_category, {})[name] = {"price": price, "unit": unit, "raw": ln}
            continue

        # غير معروف: نحفظ كسطر بدون سعر
        data_index.setdefault(current_category, {})[ln.strip()] = {"price": None, "unit": None, "raw": ln}

    # بعد التحميل: flatten names list للتطابق البحثي
    logger.info("Loaded data: %d categories, %d total items",
                len(data_index), sum(len(v) for v in data_index.values()))


# نجهز البيانات عند بدء التشغيل
load_data_file()


# -------------------------
# دوال مساعدة للبحث (fuzzy using difflib)
# -------------------------
def all_item_names() -> List[str]:
    names = []
    for cat, items in data_index.items():
        for n in items.keys():
            names.append(n)
    return names


def find_best_match(query: str) -> Tuple[Optional[str], float, Optional[str]]:
    """
    ترجع أفضل اسم مطابق، نسبة التشابه (0..1)، والفئة.
    """
    query_norm = query.strip().lower()
    candidates = []
    for cat, items in data_index.items():
        for name in items.keys():
            candidates.append((name, cat))
    # استخدم difflib لحساب التشابه
    best_name = None
    best_score = 0.0
    best_cat = None
    for name, cat in candidates:
        s = difflib.SequenceMatcher(None, query_norm, name.lower()).ratio()
        if s > best_score:
            best_score = s
            best_name = name
            best_cat = cat
    return best_name, best_score, best_cat


def search_in_data(query: str) -> List[Tuple[str, str, Dict]]:
    """
    بحث دقيق: لو نص السؤال يحتوي اسم المنتج حرفيًا أو مقطع واضح.
    يرجع قائمة نتائج (category,name,info)
    """
    q = query.strip().lower()
    results = []
    for cat, items in data_index.items():
        for name, info in items.items():
            if name.lower() in q or q in name.lower():
                results.append((cat, name, info))
    return results


def format_item_response(cat: str, name: str, info: Dict) -> str:
    # شكل الرد عند وجود معلومات
    price = info.get("price")
    unit = info.get("unit") or ""
    if price:
        return f"🧾 {name}\n💰 السعر: {price} جنيه\n📦 الوحدة: {unit}\n🏷️ التصنيف: {cat}"
    else:
        return f"🧾 {name}\n❗ السعر غير متوفر حالياً.\n📦 الوحدة: {unit or 'غير متاح'}\n🏷️ التصنيف: {cat}"


# -------------------------
# أمر التوقف/الاستئناف
# -------------------------
def is_stop_command(text: str) -> bool:
    t = text.strip().lower()
    return t in {"stop", "وقف", "سكت", "بطل", "pause", "ايقاف"}


def is_resume_command(text: str) -> bool:
    t = text.strip().lower()
    return t in {"start", "ابدأ", "رجع", "رجعلي", "resume", "تشغيل"}


# -------------------------
# helpers menu sent / pause save
# -------------------------
def recently_sent_menu(user_id: str) -> bool:
    ts = recent_menu_sent.get(user_id)
    if not ts:
        return False
    return (time.time() - ts) < MENU_CONFIRM_WINDOW


def mark_menu_sent(user_id: str):
    recent_menu_sent[user_id] = time.time()


def save_paused():
    # حفظ مؤقت لحالة الإيقاف (اختياري)
    try:
        with open(".paused.json", "w", encoding="utf-8") as f:
            json.dump(paused_users, f)
    except Exception:
        pass


def load_paused():
    global paused_users
    if os.path.exists(".paused.json"):
        try:
            with open(".paused.json", "r", encoding="utf-8") as f:
                paused_users = json.load(f)
        except Exception:
            paused_users = {}


load_paused()


# -------------------------
# optional: تحسين الصياغة باستخدام OpenAI (لو موجود المفتاح)
# -------------------------
async def refine_with_openai(text: str) -> str:
    if not OPENAI_API_KEY:
        return text
    # استدعاء ChatGPT بسيط لتحسين الصياغة (اختياري)
    try:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "gpt-4o-mini",  # اختر نموذج مناسب أو غيّره حسب توافرك
            "messages": [
                {"role": "system", "content": "أعد صياغة الرد بالغة العربية باللهجة المصرية وبإيموجي بسيط، مختصر ومحترف."},
                {"role": "user", "content": text}
            ],
            "max_tokens": 200,
            "temperature": 0.2
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            if r.status_code == 200:
                j = r.json()
                return j["choices"][0]["message"]["content"].strip()
            else:
                logger.warning("OpenAI refine failed %s", r.text)
                return text
    except Exception as e:
        logger.warning("OpenAI refine exception: %s", e)
        return text


# -------------------------
# منطق الرد على رسالة المستخدم (الكاملة)
# -------------------------
async def handle_user_message(user_id: str, text: str) -> str:
    q = text.strip()
    if not q:
        return "🙂 ممكن تبعت سؤالك أو اسم المنتج اللي محتاج سعره؟"

    # أوامر الوقف / التشغيل
    if is_stop_command(q):
        paused_users[user_id] = time.time()
        save_paused()
        append_memory(f"{time.strftime('%Y-%m-%d')} — ACTION — 'USER_PAUSED:{user_id}'")
        return "⏸️ تمام — هاسكت. لو عايزني أشتغل تاني اكتب 'start' أو 'ابدأ'."

    if is_resume_command(q):
        if user_id in paused_users:
            paused_users.pop(user_id, None)
            save_paused()
            return "▶️ تمام — شغّلت البوت تاني. ازاي أساعد حضرتك؟"
        else:
            return "🙂 البوت شغال بالفعل. ازاي أقدر أخدمك؟"

    # لو المستخدم واقع موقوف
    if user_id in paused_users:
        return "⏸️ حضرتك مختار البوت يتوقف حالياً. لو عايز ترجعه اكتب 'start' أو 'ابدأ'."

    low = q.lower()

    # لو المستخدم طلب المنيو صراحة
    if any(kw in low for kw in MENU_KEYWORDS) or low in CONFIRM_POSITIVE:
        mark_menu_sent(user_id)
        return f"📋 تفضل المنيو الكامل:\n\n{MENU_LINKS_TEXT}\n\n📩 لو محتاج سعر صنف معين اكتب اسمه بالتقريب."

    # بحث دقيق أولاً (contains)
    direct = search_in_data(q)
    if direct:
        # نأخذ أول نتيجة مفصلة
        cat, name, info = direct[0]
        resp = format_item_response(cat, name, info)
        resp += "\n\n📋 المنيو الكامل: " + "https://misrsweets.com/catalogs/"
        # نحفظ كمعلومة متكررة إن كان هذا استعلام سعر متكرر (مثال للذاكرة)
        append_memory(f"{time.strftime('%Y-%m-%d')} — QUERY — 'USER:{user_id} asked price for {name}'")
        # نُحسن الصياغة لو ممكن
        return await refine_with_openai(resp)

    # إذا النص يبدو أنه نية للسعر فنجرب fuzzy match
    if any(kw in low for kw in PRICE_KEYWORDS) or re.search(r"\d", low):
        best_name, score, cat = find_best_match(q)
        logger.info("Fuzzy match: %s (score=%.2f) for query=%s", best_name, score, q)
        if best_name and score >= SIMILARITY_THRESHOLD:
            info = data_index.get(cat, {}).get(best_name, {})
            resp = format_item_response(cat, best_name, info)
            resp += "\n\n📋 المنيو الكامل: " + "https://misrsweets.com/catalogs/"
            # سجل كذاكرة سعر مؤكد إن رغبة العميل
            append_memory(f"{time.strftime('%Y-%m-%d')} — PRICE_QUERY — '{best_name} approx_match score={score:.2f}'")
            return await refine_with_openai(resp)
        # لو ما لقيناش نتيجة مؤكدة: اسأل تأكيد أو اقترح المنيو
        if recently_sent_menu(user_id):
            return "معلش مش لاقي الصنف ده بالتحديد 🤔\nلو تحب اختار من المنيو اكتب 'منيو'."
        else:
            return "مش لاقي الصنف ده بالتحديد 😕\nتحب أبعتلك المنيو كامل عشان تختار؟ اكتب 'نعم' أو 'منيو' لو موافق."

    # لو مش طلب سعر أو منيو واضح -> نطلب توضيح ونقترح المنيو
    if recently_sent_menu(user_id):
        return "معلش مش فهمت سؤالك — تقدر تبعت اسم المنتج بالظبط أو اكتب 'منيو' تشوف القايمة كاملة."
    else:
        return "معنديش معلومة واضحة عن اللي سألت عنه 🤔\nتحب أبعتلك المنيو كامل عشان تختار؟ اكتب 'نعم' أو 'منيو'."

# -------------------------
# إرسال رسالة للفيسبوك
# -------------------------
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.error("PAGE_TOKEN not set; can't send message")
        return
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=8)
        logger.info("📤 Sent: %s | Status: %s", text[:60], r.status_code)
        return r.status_code
    except Exception as e:
        logger.error("Failed to send message: %s", e)
        return None


# -------------------------
# Endpoints
# -------------------------
@app.get("/")
def home():
    return {"status": "alive", "who": "MisrSweets Bot", "data_items": sum(len(v) for v in data_index.values())}


@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token and challenge:
        if VERIFY_TOKEN and token == VERIFY_TOKEN:
            return int(challenge)
        else:
            raise HTTPException(status_code=403, detail="Invalid verify token")
    raise HTTPException(status_code=400)


@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        logger.error("Invalid JSON payload: %s", e)
        raise HTTPException(status_code=400)
    logger.info("📩 Incoming Event: %s", body)

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                sender = messaging_event.get("sender", {}).get("id")
                # تجاهل رسائل system مثل delivery أو read
                if not sender:
                    continue
                # فقط نصوص
                msg = messaging_event.get("message", {})
                if not msg:
                    continue
                text = msg.get("text")
                if not text:
                    # لو attachments أو أشياء أخرى، نرد تلقائياً
                    send_message(sender, "📌 تقدر تبعت لنا اسم المنتج أو استفسارك كتابةً عشان نساعدك.")
                    continue
                # معالجة
                reply = await handle_user_message(sender, text)
                send_message(sender, reply)
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "ignored"})


# -------------------------
# CLI: إعادة تحميل الداتا يدوياً عبر ملف خاص (اختياري)
# لو شغلت: حذف/تعديل data.txt ثم لمس ملف reload.trigger
# -------------------------
def watch_reload_trigger():
    tfile = "reload.trigger"
    if os.path.exists(tfile):
        os.remove(tfile)
        load_data_file()
        logger.info("Data reloaded by trigger.")


# -------------------------
# تشغيل الخادم
# -------------------------
if __name__ == "__main__":
    logger.info("🚀 Starting MisrSweets Bot")
    # تحميل البيانات الأساسية
    load_data_file()
    # شغيل uvicorn
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT)
    except Exception as e:
        logger.exception("Server crashed: %s", e)