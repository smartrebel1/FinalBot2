import os
import requests
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# قراءة المتغيرات من Railway
FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

# التحقق من Webhook
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403)

# استقبال الرسائل
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"]:
                    sender = event["sender"]["id"]
                    msg = event["message"]["text"]

                    logger.info(f"👤 User {sender} says: {msg}")

                    reply = ai_reply(msg)
                    send_message(sender, reply)

    return JSONResponse({"status": "ok"})

# الذكاء الاصطناعي
def ai_reply(user_message):
    if not GROQ_API_KEY:
        return "شكراً لتواصلك! يسعدنا الرد عليك في أي وقت 💜"

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "llama3-70b-8192",
        "messages": [
            {"role": "system", "content": "أنت بوت خدمة عملاء لحلويات مصر. كن ودوداً وأجب من data.txt إذا وُجد."},
            {"role": "user", "content": user_message}
        ]
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        data = r.json()

        if "choices" in data:
            return data["choices"][0]["message"]["content"]

        logger.error(f"🔥 Groq error: {data}")
        return "عذرًا، فيه مشكلة في المعالجة دلوقتي...."

    except Exception as e:
        logger.error(f"AI error: {e}")
        return "حصل خطأ بسيط.. حاول تاني 💜"

# إرسال الرسالة لفيسبوك
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    r = requests.post(url, params=params, json=payload)
    logger.info(f"📤 Sent: {text[:30]} | Status: {r.status_code}")

# تشغيل التطبيق محليًا (اختياري)
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
