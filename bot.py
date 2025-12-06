import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING WITH LLAMA-3.3-70B-VERSATILE (GROQ)")

load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "llama-3.3-70b-versatile"

app = FastAPI()

STOP_MODE = False   # وضع الإيقاف

# =============================
#  تحميل ملف الـ DATA
# =============================
def load_data():
    if os.path.exists("data.txt"):
        return open("data.txt", "r", encoding="utf-8").read()
    return ""

# =============================
#  تحميل ملف MEMORY
# =============================
def load_memory():
    if os.path.exists("memory.txt"):
        return open("memory.txt", "r", encoding="utf-8").read()
    return ""

DATA = load_data()
MEMORY = load_memory()


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



# ======================================================
#   معالجة الرسائل + ذكاء أعلى + تصحيح إملائي بسيط
# ======================================================
async def generate_reply(user_msg: str):

    global STOP_MODE

    # وضع الإيقاف
    if user_msg.strip().lower() in ["stop", "ستوب", "قف", "اسكت"]:
        STOP_MODE = True
        return "حاضر يا فندم، هسكت دلوقتي 🤐، أول ما تحب أكمل قول *رجوع* ✨"

    if user_msg.strip().lower() in ["رجوع", "continue", "start"]:
        STOP_MODE = False
        return "تمام رجعت مع حضرتك 😊✔️"

    # لو الوضع موقوف
    if STOP_MODE:
        return "🤐…"

    # =====================================================================================
    #  البـــرمـــت — دمج DATA + MEMORY + تصحيح الإملاء + ذكاء أعلى + ايموجيز
    # =====================================================================================

    prompt = f"""
أنت بوت خدمة عملاء رسمي لشركة **حلويات مصر** 🎉.
مهمتك الرد بدقة واحتراف وبلهجة مصرية راقية ❤️.

📌 **قواعد الرد**:
- استخدم الإيموجيز المناسبة 👍🎂✨.
- لو فيه خطأ إملائي من العميل → صححه وافهم قصده.
- اعتمد فقط على البيانات الموجودة.
- لو المعلومة مش موجودة قول: "المعلومة دي مش متاحة حالياً يا فندم ❤️".
- لو العميل طلب المنيو → ابعتله الروابط فقط.
- الرد مختصر ودقيق وبدون حشو.
- لا تخترع أسعار أو منتجات غير موجودة.

======================
📦 **DATA (جميع الأسعار والمنتجات)**:
{DATA}

======================
🧠 **MEMORY (التعليمات الثابتة وسلوك البوت)**:
{MEMORY}

======================

رسالة العميل:  
{user_msg}

اعرض الرد النهائي فقط بدون شرح.
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.25
    }

    # Retry 3 مرات
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()

            else:
                logger.error(f"🔥 Groq Error Attempt {attempt+1}: {response.text}")

        except Exception as e:
            logger.error(f"⚠️ AI Error Attempt {attempt+1}: {e}")

        time.sleep(1)

    return "المعذرة يا فندم، السيرفر مشغول… حاول تاني بعد لحظات ❤️"


# ======================================================
#   استقبال webhook
# ======================================================
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

        return JSONResponse({"status": "ok"}, status_code=200)

    return JSONResponse({"status": "ignored"}, status_code=200)


# ======================================================
#   إرسال الرد إلى ماسنجر
# ======================================================
def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    r = requests.post(url, json=payload)
    logger.info(f"📤 Sent: {text[:50]} | Status: {r.status_code}")


# ======================================================
#  تشغيل السيرفر
# ======================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)