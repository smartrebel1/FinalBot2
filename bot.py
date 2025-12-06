import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
import time
import difflib

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING WITH LLAMA-3.3-70B-VERSATILE (GROQ) — SMART MODE")

load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "llama-3.3-70b-versatile"

app = FastAPI()

# ---------- UTILS ---------- #

def parse_data():
    """تحميل ملف الداتا وتحويله لقاموس {تصنيف → {اسم صنف → معلومات}}"""
    data = {}

    if not os.path.exists("data.txt"):
        return data

    current_category = None

    with open("data.txt", "r", encoding="utf8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # عنوان فئة
            if not ":" in line and not "—" in line:
                current_category = line
                data[current_category] = {}
                continue

            # عنصر
            if ":" in line and "—" in line:
                try:
                    name, rest = line.split(":", 1)
                    price, unit = rest.split("—")
                    name = name.strip()
                    price = price.strip().replace("جنيه", "").replace(" ", "")
                    unit = unit.strip()

                    data[current_category][name] = {
                        "price": price,
                        "unit": unit
                    }
                except:
                    pass

    return data


def find_best_match(data, query):
    """بحث ذكي + تقريب الكلمات"""
    query = query.strip()

    all_items = []
    for cat, items in data.items():
        for name in items.keys():
            all_items.append((cat, name))

    names_only = [name for _, name in all_items]

    match = difflib.get_close_matches(query, names_only, n=1, cutoff=0.55)

    if not match:
        return None, None

    best_name = match[0]

    for cat, name in all_items:
        if name == best_name:
            return cat, name

    return None, None


def pretty_unit(unit):
    u = unit.lower()
    if "kg" in u or "كيلو" in u:
        return "كيلو"
    if "unit" in u or "قطعة" in u:
        return "قطعة"
    return unit


def format_item_response(cat, name, info):
    price = info.get("price")
    unit = pretty_unit(info.get("unit", ""))

    return (
        f"🧾 {name}\n"
        f"💰 السعر: {price} جنيه\n"
        f"⚖️ {unit}\n"
        f"📌 القسم: {cat}"
    )

def fallback_menu_response():
    return (
        "📋 تقدر تشوف المنيو هنا:\n"
        "منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5\n"
        "منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8\n"
        "منيو التورت والحلويات الفرنسية: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5\n"
        "منيو المخبوزات والبسكويت: https://photos.app.goo.gl/YHS319dQxRBsnFdt5\n"
        "منيو الشيكولاتات والكراميل: https://photos.app.goo.gl/6JhJdUWLaTPTn1GNA\n"
        "منيو الآيس كريم والعصائر والكاسات: https://photos.app.goo.gl/boJuPbMUwUzRiRQw8\n"
        "منيو الكافيه: https://photos.app.goo.gl/G4hjcQA56hwgMa4J8\n"
        "جميع الكتالوجات: https://misrsweets.com/catalogs/\n\n"
        "📩 سيتم التواصل معك في أقرب وقت ❤️"
    )

# ---------- AI ---------- #

async def groq_reply(prompt):

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]

            logger.error(f"🔥 Groq Error Attempt {attempt+1}: {response.text}")

        except Exception as e:
            logger.error(f"⚠️ Groq Exception Attempt {attempt+1}: {e}")

        time.sleep(1)

    return None


async def generate_reply(user_msg):

    # أمر وقف البوت
    if user_msg.lower().strip() in ["stop", "وقف", "اسكت", "كفاية"]:
        return "👌 تمام يا فندم… هسكت دلوقتي. كلمني لما تحتاجني ❤️"

    data = parse_data()

    # بحث ذكي داخل الداتا
    cat, name = find_best_match(data, user_msg)

    if cat and name:
        info = data[cat][name]
        return format_item_response(cat, name, info)

    # لو مفيش تطابق → fallback منطقي
    return fallback_menu_response()


# ---------- WEBHOOK ---------- #

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

    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body["entry"]:
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:

                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]

                    logger.info(f"👤 User {sender} says: {text}")

                    reply = await generate_reply(text)
                    send_message(sender, reply)

        return JSONResponse({"status": "ok"})

    return JSONResponse({"status": "ignored"})

def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    r = requests.post(url, json=payload)
    logger.info(f"📤 Sent: {text[:40]} | Status: {r.status_code}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)