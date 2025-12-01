import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 BOT RUNNING WITH llama-3.2-90b-text-preview (NEW GROQ MODEL)")

load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# تحميل بيانات الشركة من data.txt
COMPANY_DATA = ""
if os.path.exists("data.txt"):
    COMPANY_DATA = open("data.txt", "r", encoding="utf8").read()


app = FastAPI()


@app.get("/")
def home():
    return {
        "status": "alive",
        "model": "llama-3.2-90b-text-preview"
    }


@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ Webhook Verified Successfully!")
        return int(challenge)

    raise HTTPException(status_code=403)


# --------------------------
# 🤖 AI Reply (Groq)
# --------------------------
async def groq_reply(user_message: str) -> str:

    if not GROQ_API_KEY:
        return "مشكلة في السيرفر حالياً."

    url = "https://api.groq.com/openai/v1/chat/completions"

    system_prompt = f"""
أنت مساعد محترف يعمل لصالح (حلويات مصر).

مهمتك:
- الرد على العملاء بأقصى دقة.
- استخدم **فقط** المعلومات التالية من الشركة:

{COMPANY_DATA}

قواعد مهمة:
1. لا تخترع أي معلومة غير موجودة.
2. لو العميل سأل عن شيء مش موجود → رد: "السؤال خارج نطاق المعلومات المتاحة."
3. الردود قصيرة، محترمة، وواضحة.
"""

    payload = {
        "model": "llama-3.2-90b-text-preview",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        data = response.json()
        logger.error(f"🔥 Groq Full Response: {data}")

        if response.status_code != 200:
            return "السيرفر مشغول حالياً."

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        logger.error(f"❌ AI Error: {e}")
        return "عذراً، حدث خطأ أثناء المعالجة."


# --------------------------
# ✉ إرسال رسالة للماسنجر
# --------------------------
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}

    try:
        r = requests.post(url, params=params, json=payload)
        logger.info(f"📤 Sent: {text[:50]} | Status: {r.status_code}")
    except Exception as e:
        logger.error(f"🔥 FB Send Error: {e}")


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):

                if "message" in event and "text" in event["message"]:
                    sender = event["sender"]["id"]
                    text = event["message"]["text"]

                    reply = await groq_reply(text)
                    send_message(sender, reply)

    return JSONResponse({"status": "ok"})
