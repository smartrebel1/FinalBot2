import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn
import time

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# المتغيرات (هتيجي من Railway Variables)
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

MODEL = "llama-3.3-70b-versatile"

app = FastAPI()

# قراءة ملف البيانات
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded from data.txt")
except Exception as e:
    logger.error(f"⚠️ Error loading data.txt: {e}")
    KNOWLEDGE_BASE = "لا توجد معلومات متاحة حالياً."

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
    system_prompt = f"""
    أنت مساعد خدمة عملاء ذكي ومحترم لشركة "حلويات مصر" (Misr Sweets).
    مهمتك الرد باللهجة المصرية الودودة بناءً *فقط* على البيانات التالية.

    === البيانات ===
    {KNOWLEDGE_BASE}
    ===============

    تعليمات:
    1. لا تؤلف معلومات غير موجودة.
    2. لو العميل سأل عن المنيو، ابعتله روابط المنيو من البيانات.
    3. خليك مختصر ومفيد.
    4. لو المعلومة مش موجودة قول: "المعلومة دي مش عندي حالياً، ممكن تتصل بالفرع".

    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.3
    }

    # محاولة الاتصال بـ Groq
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                logger.error(f"Groq Error: {response.text}")
                return "معلش في مشكلة فنية بسيطة، حاول تاني."
        except Exception as e:
            logger.error(f"Connection Error: {e}")
            return "النظام مشغول حالياً، جرب كمان شوية."

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body["entry"]:
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    # الرد
                    reply = await generate_reply(text)
                    send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)
