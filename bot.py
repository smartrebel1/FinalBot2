import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
import time

# ---------------------------------------------------------
# 🔥 النظام التشغيلي
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING WITH LLAMA-3.2-32B (GROQ STABLE MODEL)")

# ---------------------------------------------------------
# 📌 تحميل المتغيرات
# ---------------------------------------------------------
load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "llama-3.2-32b-text-preview"   # أقوى موديل مستقر حاليًا

app = FastAPI()


# ---------------------------------------------------------
# 🩺 Health Check
# ---------------------------------------------------------
@app.get("/")
def home():
    return {"status": "alive", "model": MODEL}


# ---------------------------------------------------------
# 🔐 Webhook Verification
# ---------------------------------------------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)

    raise HTTPException(status_code=403)


# ---------------------------------------------------------
# 🤖 AI Reply Function with Retry
# ---------------------------------------------------------
async def generate_reply(user_msg: str):

    # نقرأ ملف البيانات
    data_text = ""
    if os.path.exists("data.txt"):
        data_text = open("data.txt", "r", encoding="utf-8").read()

    prompt = f"""
أنت بوت محترف لخدمة عملاء حلويات مصر.
استخدم المعلومات التالية فقط ولا تخترع أي شيء من خارجها:

===== DATA =====
{data_text}
================

عند الرد:
- كن مهذب وبسيط.
- استخدم لهجة مصرية محترمة.
- لو سؤال خارج البيانات قل: "المعلومة دي مش موجودة عندي حالياً، تقدر تسألنا في الفروع".

رسالة العميل: {user_msg}
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    # نظام Retry تلقائي 3 مرات
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                ai_text = response.json()["choices"][0]["message"]["content"]
                return ai_text.strip()

            else:
                logger.error(f"🔥 Groq Error Attempt {attempt+1}: {response.text}")

        except Exception as e:
            logger.error(f"⚠️ AI Error Attempt {attempt+1}: {e}")

        time.sleep(1)  # انتظار بين المحاولات

    return "الخدمة مشغولة دلوقتي يا فندم… حاول تاني بعد لحظات ❤️"


# ---------------------------------------------------------
# 📩 استقبال رسائل فيسبوك
# ---------------------------------------------------------
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


# ---------------------------------------------------------
# 📤 إرسال الرد لفيسبوك
# ---------------------------------------------------------
def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    r = requests.post(url, json=payload)
    logger.info(f"📤 Sent: {text[:40]} | Status: {r.status_code}")


# ---------------------------------------------------------
# 🚀 تشغيل السيرفر
# ---------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
