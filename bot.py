# bot.py
# نسخة متكاملة لبوت فيسبوك (FastAPI) + بحث ذكي في data.txt + memory + STOP
import os
import logging
import requests
import difflib
import json
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
from typing import Dict, Tuple, List

# ----- إعداد الـ logger -----
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("bot")
logger.info("🚀 RUNNING NEW BOT VERSION - CATEGORY DATA MODE (A)")

# ----- تحميل المتغيرات -----
load_dotenv()
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "my_verify_token_123")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
# استخدم أي مزوّد AI: إما OPENAI أو GROQ أو DeepSeek — لو مش حاطط مفتاح، البوت يشتغل بقواعد محلية.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# خيارات
AI_PROVIDER = os.getenv("AI_PROVIDER", "OPENAI")  # OPENAI | GROQ | NONE

DATA_FILE = "data.txt"
MEMORY_FILE = "memory.txt"
PAUSE_FILE = "paused.json"   # لحفظ حالة STOP للمستخدمين (persist)
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.65))

app = FastAPI(title="MisrSweets Bot")

# ----- تحميل الحالات الموقوفة (paused users) -----
def load_paused() -> Dict[str, float]:
    if os.path.exists(PAUSE_FILE):
        try:
            return json.load(open(PAUSE_FILE, "r", encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_paused(d):
    json.dump(d, open(PAUSE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

paused_users = load_paused()

# ----- تحميل البيانات من data.txt بشكل منظم -----
# نتوقع data.txt بصيغة: CATEGORY | SKU | ITEM NAME | UNIT | PRICE
def load_data() -> Dict[str, Dict[str, Dict]]:
    data = {}
    if not os.path.exists(DATA_FILE):
        logger.warning("data.txt not found.")
        return data
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # نتعامل مع الفاصل " | "
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                # محاولة دعم خطوط قديمة: CATEGORY | ITEM — PRICE — UNIT
                # إذن نعالج بأمان
                # إذا كان طول 3: category|item|price
                if len(parts) == 3:
                    cat, name, price = parts
                    sku = ""
                    unit = ""
                else:
                    continue
            else:
                cat, sku, name, unit, price = parts[:5]
            if not cat:
                cat = "عام"
            data.setdefault(cat, {})
            # المفتاح للبحث: name lowercase
            key = name.strip()
            data[cat][key] = {"sku": sku, "name": name, "unit": unit, "price": price}
    return data

# تحميل الميموري (ملف بسيط للنصوص)
def load_memory() -> str:
    if os.path.exists(MEMORY_FILE):
        return open(MEMORY_FILE, "r", encoding="utf-8").read()
    return ""

def append_memory(line: str):
    # يحفظ سطرًا جديدًا في memory.txt
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")

data_index = load_data()
memory_text = load_memory()

# ----- وظائف البحث الذكي -----
def all_item_names() -> List[str]:
    names = []
    for cat in data_index:
        names.extend(list(data_index[cat].keys()))
    return names

def find_best_match(query: str) -> Tuple[str, float, str]:
    """
    يرجع: (matched_name, score, category)
    يستخدم difflib SequenceMatcher عبر جميع الأسماء.
    """
    query = query.strip().lower()
    candidates = all_item_names()
    if not candidates:
        return "", 0.0, ""
    # استخدم get_close_matches أو ratio
    best = ("", 0.0, "")
    for cat in data_index:
        for name in data_index[cat]:
            score = difflib.SequenceMatcher(None, query, name.lower()).ratio()
            if score > best[1]:
                best = (name, score, cat)
    return best

def search_in_data(query: str):
    """
    بحث مباشر: لو اسم الصنف ظاهر ككلمة داخل الاسم.
    """
    q = query.strip().lower()
    results = []
    for cat in data_index:
        for name, info in data_index[cat].items():
            if q == name.lower() or q in name.lower():
                results.append((cat, name, info))
    return results

# ----- صياغة الردود -----
MENU_LINKS_TEXT = """منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5
منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8
منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5
منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5
منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA
منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8
منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8
جميع الكتالوجات: https://misrsweets.com/catalogs/"""

def format_item_response(cat: str, name: str, info: Dict) -> str:
    price = info.get("price", "غير متاح")
    unit = info.get("unit", "غير متاح")
    lines = []
    # إيموجي خفيف
    lines.append(f"🧾 {name}")
    lines.append(f"💰 السعر: {price}")
    lines.append(f"📦 الوحدة: {unit}")
    lines.append(f"🏬 القسم: {cat}")
    return "\n".join(lines)

def fallback_menu_response() -> str:
    s = "هذا ملخص المنيو والكتالوجات عندنا — تقدر تشوف القوائم كاملة هنا: \n\n"
    s += MENU_LINKS_TEXT
    s += "\n\n📩 لو عايز سعر صنف معين اكتب اسم الصنف تقريبًا، ولو حبيت أأكد سعر معين اكتب: تأكيد سعر <اسم الصنف> — علشان أضيفه للذاكرة."
    return s

# ----- AI / Generator (اختياري) -----
async def ai_refine_reply(raw_prompt: str) -> str:
    """
    واجهة اختيارية لموديل خارجي (OpenAI أو Groq).
    لو المفتاح غير متوفر، ترجع raw_prompt مباشرة أو يتم تبسيطها.
    """
    if AI_PROVIDER.upper() == "OPENAI" and OPENAI_API_KEY:
        # استخدم OpenAI Chat Completions v1
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        payload = {
            "model": "gpt-4o-mini",  # تغيير حسب حسابك
            "messages": [
                {"role": "system", "content": "أجب بالعربية باللهجة المصرية وباختصار، استخدم المعلومات المقدمة فقط."},
                {"role": "user", "content": raw_prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 400
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code == 200:
                    j = r.json()
                    text = j["choices"][0]["message"]["content"].strip()
                    return text
                else:
                    logger.error("OpenAI error: %s", r.text)
        except Exception as e:
            logger.error("OpenAI exception: %s", e)
        return raw_prompt

    # Groq أو مزوّد آخر يمكن إضافته هنا (استخدم GROQ_API_KEY)
    # وإلا نعيد raw prompt
    return raw_prompt

# ----- معالجة أوامر STOP و resume -----
STOP_WORDS = {"stop", "سكت", "وقف", "بطل", "كفاية", "وقف الكلام"}
RESUME_WORDS = {"start", "ابدأ", "رجع", "كمل", "resume", "استأنف"}

def is_stop_command(text: str) -> bool:
    t = text.strip().lower()
    return any(t == w or t.startswith(w + " ") for w in STOP_WORDS)

def is_resume_command(text: str) -> bool:
    t = text.strip().lower()
    return any(t == w or t.startswith(w + " ") for w in RESUME_WORDS)

# ----- إرسال رسالة لفيسبوك -----
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.warning("PAGE_TOKEN not set — cannot send message.")
        return
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=10)
        logger.info("📤 Sent to %s | status: %s", user_id, r.status_code)
    except Exception as e:
        logger.error("Error sending to FB: %s", e)

# ----- المنطق الرئيسي للرد -----
async def handle_user_message(user_id: str, text: str) -> str:
    # حالة STOP
    if is_stop_command(text):
        paused_users[user_id] = time.time()
        save_paused(paused_users)
        return "⏸️ موافق — هاسكت. لما تحب نكمل اكتب: start أو ابدأ."

    if is_resume_command(text):
        if user_id in paused_users:
            paused_users.pop(user_id, None)
            save_paused(paused_users)
            return "▶️ تمام — حاضر، نكمل."
        else:
            return "🙂 البوت شغال بالفعل. كيف أقدر أخدمك؟"

    # لو المستخدم موقوف (سبق وطلب STOP) — لا نرد إلا resume
    if user_id in paused_users:
        return "⏸️ أنت طلبت البوت يتوقف — اكتب 'start' أو 'ابدأ' لو عايز ترجع الردود."

    # تنظيف الطلب
    q = text.strip()

    # استعلام مباشر (مطابقة بسيطة)
    direct = search_in_data(q)
    if direct:
        # لو لقاها بالاسم بالضبط أو داخل الاسم
        # نرد بأول نتيجة واضحة
        cat, name, info = direct[0]
        resp = format_item_response(cat, name, info)
        # نقترح روابط المنيو لو حابب
        resp += "\n\n📋 للمنيو الكامل: " + "https://misrsweets.com/catalogs/\n😊 لو عايز أضيف سعر جديد تأكد بكتابة: تأكيد سعر <اسم الصنف> — وسأحفظه."
        return await ai_refine_reply(resp)

    # لو مفيش نتيجة مباشرة -> نحاول المطابقة التقريبية
    match_name, score, cat = find_best_match(q)
    logger.info("Best match: %s (score=%s) in %s", match_name, score, cat)
    if score >= SIMILARITY_THRESHOLD:
        info = data_index.get(cat, {}).get(match_name)
        resp = format_item_response(cat, match_name, info)
        # إذا المطابقة منخفضة لكن مقبولة نعرض "تقصد؟"
        if score < 0.9:
            resp += f"\n\nهل تقصد: «{match_name}»؟ لو لا اكتب الاسم تاني أو اكتب 'منيو' عشان أبعتهولك."
        resp += "\n\n📋 المنيو الكامل: https://misrsweets.com/catalogs/"
        return await ai_refine_reply(resp)

    # لو لم يجد أي مطابقة جيدة -> نعرض المنيو الكامل أولاً (طلبك)
    # ثم نقول سنتواصل معك
    resp = "أنا مبعتلك المنيو دلوقتي عشان تختار 🔽\n\n" + MENU_LINKS_TEXT + "\n\n📩 سيتم التواصل معك في أقرب وقت لو احتجنا توضيح. لو عايز سعر صنف معين اكتب اسمه تقريبًا."
    return resp

# ----- endpoint التحقق (Facebook webhook verify) -----
@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return int(challenge)
    logger.warning("Webhook verification failed.")
    raise HTTPException(status_code=403, detail="Verification failed")

# ----- endpoint استقبال الرسائل -----
@app.post("/webhook")
async def fb_webhook(request: Request):
    body = await request.json()
    logger.info("📩 Incoming Event: %s", body)
    # عملية بسيطة للتعامل مع page messages
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                # بعض الاحداث delivery أو read
                if "message" in event:
                    sender_id = str(event["sender"]["id"])
                    # لو الرسالة نصية
                    if "text" in event["message"]:
                        text = event["message"]["text"]
                        logger.info("👤 User %s says: %s", sender_id, text)
                        reply_text = await handle_user_message(sender_id, text)
                        send_message(sender_id, reply_text)
                    else:
                        # رد افتراضي على attachments
                        send_message(sender_id, "🙏 استلمت رسالتك، لو حبّيت اكتب اسم المنتج اللي محتاجه أو 'منيو' لأبعتلك القوائم.")
        return JSONResponse({"status": "EVENT_RECEIVED"}, status_code=200)
    return JSONResponse({"status": "IGNORED"}, status_code=200)

# ----- أدوات مساعدة لإضافة سعر جديد للذاكرة (تأكد قبل الاضافة) -----
def confirm_and_store_price(item_name: str, price: str, unit: str = ""):
    # هذه الدالة تُكتب في حالة تأكيد منك يدوياً عبر واجهة/أمر
    # تضيف سطر في memory.txt ولمساته تحتاج تحقق بشري لاحقاً
    now = time.strftime("%Y-%m-%d")
    line = f"{now} — PRICE_UPDATE — \"{item_name}\" — {price} — {unit}"
    append_memory(line)
    logger.info("Memory appended: %s", line)
    return line

# ----- نقطة بداية التشغيل -----
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info("Starting server on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)