# bot.py
import os
import re
import json
import time
import logging
from typing import List, Tuple, Dict, Optional

import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from rapidfuzz import process, fuzz

# ---------- إعداد اللوج ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

logger.info("🚀 BOT STARTING — Intelligent fuzzy search enabled")

# ---------- تحميل المتغيرات ----------
load_dotenv()
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN", "my_verify_token_123")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
MENU_LINKS = os.getenv("MENU_LINKS", "منيو الحلويات المصرية: https://misrsweets.com/catalogs/")  # يمكن وضع روابط متعددة مفصولة بـ ||
DATA_FILE = os.getenv("DATA_FILE", "data.txt")
MEMORY_FILE = os.getenv("MEMORY_FILE", "memory.json")

# ---------- إعدادات البحث ----------
TOP_K = 3                     # عدد الاقتراحات عند عدم التطابق الكامل
FUZZY_SCORE_THRESHOLD = 75    # عتبة قبول التطابق القوي (0-100). 75 مناسب على وضع 1 (ذكاء عالي)
FUZZY_SUGGEST_THRESHOLD = 40  # لو النتيجة أقل من هذه يقترح المنيو بدل السعر مباشرة

# ---------- FastAPI ----------
app = FastAPI(title="Misr Sweets Bot")

# ---------- ملفات الذاكرة (حالة الـ stop, updates) ----------
_memory = {
    "paused_users": [],   # قائمة user ids مُعلّقة
    "price_updates": []   # سيتم إضافة عناصر: {"date": "...", "type":"PRICE_UPDATE","content": "..."}
}

def load_memory():
    global _memory
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                _memory = json.load(f)
        except Exception as e:
            logger.error("Failed to load memory.json: %s", e)

def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save memory.json: %s", e)

load_memory()

# ---------- تحميل وتهيئة بيانات المنتج من data.txt ----------
# نتوقع data.txt فيه سطور بأشكال مرنة مثل:
# بسبوسة سادة: 130 — KG
# جاتوة اورجينال|45|Unit
# أو تنسيقات سابقة. الدالة التالية تحاول استخراج (اسم, سعر, وحدة)
def parse_line(line: str) -> Optional[Dict]:
    line = line.strip()
    if not line:
        return None

    # لو السطر يحتوي ':' أو '—' أو '|' أو tab أو ',' سنحاول التفصيل
    # نماذج شائعة: "اسم: 130 — KG" أو "اسم|130|KG" أو "اسم , 130 = KG"
    # استخدام regex لاستخراج رقم السعر و(وحدة) إن وجدت
    # أولاً حاول تقسيم بواسطة |
    for sep in ["|", "\t", ","]:
        if sep in line:
            parts = [p.strip() for p in line.split(sep) if p.strip()]
            if len(parts) >= 2:
                name = parts[0]
                price = None
                unit = ""
                # حاول إيجاد أول عدد في الأجزاء اللاحقة
                for p in parts[1:]:
                    m = re.search(r"(\d+(?:[\.,]\d+)?)", p)
                    if m:
                        price = m.group(1).replace(",", ".")
                        # كلمة الوحدة هي الباقي بعد السعر إن وُجد
                        unit_match = re.sub(r"(\d+(?:[\.,]\d+)?)", "", p).strip()
                        if unit_match:
                            unit = unit_match
                        break
                return {"name": name, "price": price or "", "unit": unit or ""}
    # لو لا توجد فواصل، نجرب أنماط أخرى
    # نمط "اسم: 130 — KG" أو "اسم: 130 KG"
    m = re.match(r"^(?P<name>.+?)[\:\-–—]\s*(?P<price>\d+(?:[\.,]\d+)?)(?:\s*[\—\-–:]+\s*(?P<unit>\w+))?", line)
    if m:
        return {"name": m.group("name").strip(), "price": m.group("price").replace(",", "."), "unit": (m.group("unit") or "").strip()}
    # نمط "اسم ... 130 جنيه"
    m2 = re.match(r"^(?P<name>.+?)\s+(\:)?\s*(?P<price>\d+(?:[\.,]\d+)?)\s*(?:ج|جنيه|EGP|kg|KG|Unit|unit)?", line, re.IGNORECASE)
    if m2:
        return {"name": m2.group("name").strip(), "price": m2.group("price").replace(",", "."), "unit": ""}
    # كملية اسمية فقط (بدون سعر)
    return {"name": line, "price": "", "unit": ""}

def load_data() -> List[Dict]:
    items = []
    if not os.path.exists(DATA_FILE):
        logger.warning(f"{DATA_FILE} not found — create it and add products")
        return items
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            parsed = parse_line(raw)
            if parsed:
                items.append(parsed)
    logger.info("Loaded %d data items from %s", len(items), DATA_FILE)
    return items

DATA_ITEMS = load_data()

# ---------- مساعدة: تنظيف وتطبيع النص للبحث ----------
def normalize(text: str) -> str:
    text = text.strip().lower()
    # إزالة حركات وعلامات شائعة، وتبديل ألفات/تهميزات تكرارية
    reps = [
        ("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"),
        ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي"), ("ّ", ""),
        ("ٌ", ""), ("ً", ""), ("ٍ", ""), ("َ", ""), ("ُ", ""), ("ِ", "")
    ]
    for a,b in reps:
        text = text.replace(a,b)
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)  # اترك عربي وحروف وأرقام
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ---------- دالة البحث الذكي ----------
def find_best_matches(query: str, top_k: int = TOP_K) -> List[Tuple[Dict, float]]:
    if not DATA_ITEMS:
        return []
    names = [item["name"] for item in DATA_ITEMS]
    # نستخدم rapidfuzz process.extract للبحث السريع
    results = process.extract(query, names, scorer=fuzz.WRatio, limit=top_k)
    # results: list of tuples (matched_name, score, index)
    output = []
    for match in results:
        matched_name, score, idx = match
        output.append((DATA_ITEMS[idx], score))
    return output

# ---------- صياغة الرد بالعربية مع ايموجي ----------
def format_product_response(item: Dict) -> str:
    name = item.get("name","")
    price = item.get("price","غير متاح")
    unit = item.get("unit","")
    unit_display = unit if unit else "غير محددة"
    if price == "":
        price = "غير متاح"
    # رد مختصر
    return f"🧾 {name}\n💰 السعر: {price} جنيه\n📦 الوحدة: {unit_display}"

def format_suggestions(matches: List[Tuple[Dict,float]]) -> str:
    lines = []
    for item, score in matches:
        lines.append(f"• {item.get('name')} — {item.get('price') or 'السعر غير متاح'} جنيه")
    if not lines:
        return ""
    return "🔎 أقرب النتائج:\n" + "\n".join(lines)

# ---------- ارسال رسالة لفيسبوك ----------
def send_message(user_id: str, text: str):
    if not PAGE_TOKEN:
        logger.warning("No PAGE_TOKEN configured — message not sent")
        return
    url = f"https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        r = requests.post(url, params=params, json=payload, timeout=8)
        logger.info("📤 Sent (status %s): %s", r.status_code, text[:80])
    except Exception as e:
        logger.error("Failed to send FB message: %s", e)

# ---------- نقاط النهاية ----------
@app.get("/")
def home():
    return {"status": "alive", "items_loaded": len(DATA_ITEMS)}

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
            for messaging in entry.get("messaging", []):
                sender = messaging.get("sender", {}).get("id")
                # تجاهل الرسائل الغير نصية
                if "message" not in messaging or "text" not in messaging["message"]:
                    continue
                text = messaging["message"]["text"].strip()
                if not text:
                    continue

                # تحقق الـ stop/resume من الذاكرة
                if sender in _memory.get("paused_users", []):
                    logger.info("User %s is paused — ignoring message", sender)
                    # نسمح لأمر resume
                    if normalize(text) in ["resume","start","استئناف","كمل"]:
                        _memory["paused_users"].remove(sender)
                        save_memory()
                        send_message(sender, "🔔 تم استئناف الردود. أنا تحت أمرك الآن 🙂")
                    else:
                        # لا نرد ببرود: نرسل رسالة موجزة أن البوت متوقف للمستخدم
                        send_message(sender, "البوت في وضع التوقف عند طلبك. اكتب `resume` لو حابب أرجع أرد.")
                    continue

                # أوامر إدارية محلية
                ntext = normalize(text)
                if ntext in ["stop","ايقاف","وقف","قف"]:
                    if sender not in _memory.get("paused_users", []):
                        _memory.setdefault("paused_users", []).append(sender)
                        save_memory()
                    send_message(sender, "⏸️ تم إيقاف الردود لحضرتك. اكتب `resume` لو حابب أرجع أشتغل.")
                    continue

                if ntext in ["menu","المنيو","منيو","قائمة","قائمةالأسعار"]:
                    # نرسل روابط المنيو مباشرة
                    links = MENU_LINKS.replace("|", "\n")
                    send_message(sender, f"📋 المنيو الكامل:\n{links}\n\n📩 لو محتاج سعر صنف محدد اكتب اسمه بالظبط أو اقرب شكل ليه.")
                    continue

                # الآن البحث الذكي في الداتا
                query = normalize(text)
                matches = find_best_matches(query, top_k=TOP_K)

                # لو التطابق الأول قوي نرد مباشرة
                if matches:
                    best_item, best_score = matches[0]
                    logger.info("Best match score: %s for %s", best_score, best_item.get("name"))
                    if best_score >= FUZZY_SCORE_THRESHOLD:
                        # رد مفصل للعنصر
                        resp = format_product_response(best_item)
                        # لو السعر غير متاح نقترح الاقرب
                        if not best_item.get("price"):
                            suggestions = format_suggestions(matches)
                            resp = f"{resp}\n\n{suggestions}\n\n📩 سيتم التواصل معك في أقرب وقت." if suggestions else f"{resp}\n\n📋 {MENU_LINKS}\n📩 سيتم التواصل معك في أقرب وقت."
                        send_message(sender, resp)
                        continue
                    # لو النتيجة متوسطة — اعرض أقرب النتائج واطلب تأكيد
                    elif best_score >= FUZZY_SUGGEST_THRESHOLD:
                        suggestions = format_suggestions(matches)
                        send_message(sender, f"📋 مش لاقي تطابق قوي، لكن دي أقرب الحاجات:\n{suggestions}\n\nلو عايزني أعرض أي واحدة منهم بوضوح اكتب اسمها أو اختار رقم from 1-{len(matches)}.")
                        continue
                    else:
                        # نتائج ضعيفة → أرسل المنيو وروابطه + تواصل
                        links = MENU_LINKS.replace("|", "\n")
                        send_message(sender, f"📋 ممكن تلاقي كل الأصناف هنا:\n{links}\n\n📩 مش لاقي المنتج ده عندي بدقة — هنرجع نتابع معاك قريبًا.")
                        continue
                else:
                    # لا بيانات على الاطلاق
                    links = MENU_LINKS.replace("|", "\n")
                    send_message(sender, f"📋 المنيو الكامل:\n{links}\n\n📩 سيتم التواصل معك في أقرب وقت.")
                    continue

        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "ignored"})

# ---------- نقطة التشغيل ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("bot:app", host="0.0.0.0", port=port, reload=False)