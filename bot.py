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

async def generate_reply(user_msg: str):

    # تحميل ملف البيانات
    data_text = ""
    if os.path.exists("data.txt"):
        data_text = open("data.txt", "r", encoding="utf-8").read()

    prompt = f"""
أنت بوت خدمة عملاء رسمي لشركة حلويات مصر.
استخدم المعلومات التالية فقط للرد:

===== DATA =====
{data_text}
================

طريقة الرد:
- لهجة مصرية محترمة.
- رد مختصر وواضح.
- لا تخترع معلومات غير موجودة في DATA.
- لو المعلومة غير موجودة قل: "المعلومة دي مش موجودة عندي حالياً".

رسالة العميل: {user_msg}
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    # Retry 3 مرات
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()

            else:
                logger.error(f"🔥 Groq Error Attempt {attempt+1}: {response.text}")

        except Exception as e:
            logger.error(f"⚠️ AI Error Attempt {attempt+1}: {e}")

        time.sleep(1)

    return "للأسف السيرفر مشغول حالياً… حاول تاني يا فندم ❤️"

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

def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    r = requests.post(url, json=payload)
    logger.info(f"📤 Sent: {text[:40]} | Status: {r.status_code}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
