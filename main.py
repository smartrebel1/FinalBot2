import os
import json
import requests
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# -----------------------------
# Logging
# -----------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# Environment Variables
# -----------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

# -----------------------------
# FastAPI App
# -----------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive", "message": "Groq LLaMA3 Bot Running"}

# -----------------------------
# Facebook Webhook Verification
# -----------------------------
@app.get("/webhook")
def verify(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        logger.info("✔ Webhook Verified Successfully")
        return int(challenge)

    raise HTTPException(status_code=403, detail="Verification failed")

# -----------------------------
# Receive Messages from Facebook
# -----------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"]:
                    sender_id = event["sender"]["id"]
                    text_received = event["message"]["text"]

                    logger.info(f"👤 User {sender_id} says: {text_received}")

                    reply_text = ai_reply(text_received)

                    send_message(sender_id, reply_text)

    return JSONResponse({"status": "ok"}, status_code=200)

# -----------------------------
# AI Reply using Groq (New LLaMA Model)
# -----------------------------
def ai_reply(user_text):

    # Load company data
    data = ""
    if os.path.exists("data.txt"):
        with open("data.txt", "r", encoding="utf-8") as f:
            data = f.read()

    prompt = f"""
أنت بوت خدمة عملاء لمحل "حلويات مصر".
هذه بيانات الشركة:

{data}

التعليمات:
- الرد يكون مختصر ومباشر.
- الود مهم.
- استخدم المعلومات فقط، ولا تخترع بيانات جديدة.
- تحدث باللهجة المصرية.
سؤال العميل: {user_text}
"""

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": "أنت مساعد خدمة عملاء محترف."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        r = requests.post(url, headers=headers, json=payload)
        res = r.json()

        logger.error(f"🔥 Groq Full Response: {res}")

        if "choices" in res:
            return res["choices"][0]["message"]["content"]

        return "عذرًا، فيه مشكلة في المعالجة دلوقتي."

    except Exception as e:
        logger.error(f"Groq Error: {e}")
        return "في مشكلة تقنية دلوقتي — حاول بعد شوية."

# -----------------------------
# Send Reply to Facebook
# -----------------------------
def send_message(user_id, text):

    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={FACEBOOK_PAGE_ACCESS_TOKEN}"

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, json=payload)
        logger.info(f"📤 Sent: {text[:40]}... | Status: {r.status_code}")

    except Exception as e:
        logger.error(f"Facebook Send Error: {e}")

# -----------------------------
# Run on Railway Port
# -----------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Starting on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
