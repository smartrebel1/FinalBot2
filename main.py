import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
FACEBOOK_VERIFY_TOKEN = os.getenv("my_verify_token_123
")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("EAAc4O5PZCrpoBQH2k5uAbTLbHoUIJULD8raOYaw1SZCI7fYaV5VZCZAVb7XObXbsPOy6OVp6tM5UGESoCOHMFMr2B9iogTy8WwaNclUF0Lu4WydifzVIMP4S1WHmgkE8aDDQfUthC1xkY8ZC7UCsKg5nQqEXGqNfUZAQupP0O5fMJkHdSY9wkW3sCRBgM8pmn9lcX6ClE0jyuqMe9TkQUCwjZCEBQZDZD")
# GEMINI_API_KEY optional

app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                if "message" in messaging_event and "text" in messaging_event["message"]:
                    sender_id = messaging_event["sender"]["id"]
                    text = messaging_event["message"]["text"]
                    reply = simple_ai_reply(text)
                    send_message(sender_id, reply)
    return JSONResponse({"status": "ok"})

def simple_ai_reply(text):
    # مؤقت: يرد نفس النص أو يمكنك تعديل ليستخدم data.txt لاحقًا
    if os.path.exists("data.txt"):
        data = open("data.txt", encoding="utf8").read()
        # بسيط: لو كلمة من data موجودة يرد بمعلومة
        for line in data.splitlines():
            if line.strip() and line.split(":")[0].strip() in text:
                return line
    return "شكراً لتواصلك! سنرد عليك قريباً."

def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        requests.post(url, params=params, json=payload)
    except Exception as e:
        logger.error(f"FB send error: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
