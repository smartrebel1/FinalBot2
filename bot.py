import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx

# -----------------------------------------------------
# 🔥 إعداد اللوج
# -----------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

logger.info("🚀 RUNNING NEW BOT VERSION WITH LLAMA 3.1 INSTANT MODEL")

# -----------------------------------------------------
# تحميل المتغيرات
# -----------------------------------------------------
load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# -----------------------------------------------------
# FastAPI
# -----------------------------------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "model": "llama-3.1-8b-instant"}


# -----------------------------------------------------
# ✔ Webhook Verify
# -----------------------------------------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("✅ Webhook Verified Successfully!")
        return int(challenge)

    logger.warning("❌ Webhook Verification Failed")
    raise HTTPException(status_code=403)


# -----------------------------------------------------
# 🤖 استدعاء Groq LLM
# -----------------------------------------------------
async def groq_reply(user_message: str) -> str:
    if not GROQ_API_KEY:
        logger.error("❌ No GROQ_API_KEY found — using fallback text")
        return "عذرًا، السيرفر مش قادر يعالج الرسالة دلوقتي."

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {
                "role": "system",
                "content": "أنت مساعد ذكي لصفحة (حلويات مصر). رد بشكل مختصر ومفيد وباحترام."
            },
            {
                "role": "user",
                "content": user_message
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=payload)

        full_json = response.json()
        logger.error(f"🔥 Groq FULL Response: {full_json}")

        if response.status_code != 200:
            return "عذرًا، فيه مشكلة في السيرفر دلوقتي."

        ai_text = full_json["choices"][0]["message"]["content"]
        return ai_text

    except Exception as e:
        logger.error(f"❌ Groq Exception: {e}")
        return "عذرًا، حصل خطأ أثناء المعالجة."


# -----------------------------------------------------
# ✉ إرسال رسالة للماسنجر
# -----------------------------------------------------
def send_message(user_id: str, text: str):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, params=params, json=payload)
        logger.info(f"📤 Sent: {text[:50]} | Status: {r.status_code}")
    except Exception as e:
        logger.error(f"🔥 Facebook Send Error: {e}")


# -----------------------------------------------------
# 📩 استقبال الرسائل من ماسنجر
# -----------------------------------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):

                # 👤 رسالة نصية واردة
                if "message" in messaging_event and "text" in messaging_event["message"]:
                    sender = messaging_event["sender"]["id"]
                    text = messaging_event["message"]["text"]

                    logger.info(f"👤 User {sender} says: {text}")

                    ai_reply = await groq_reply(text)

                    send_message(sender, ai_reply)

    return JSONResponse({"status": "ok"})
