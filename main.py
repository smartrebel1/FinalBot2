import os
import json
import requests
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# --------------------------------------------
# إعداد اللوج
# --------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------
# تحميل المتغيرات من Railway
# --------------------------------------------
FACEBOOK_VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")

# --------------------------------------------
# إنشاء تطبيق FastAPI
# --------------------------------------------
app = FastAPI()

# ---------------------------------------------------
# نقطة الفحص الأساسية — Railway Health Check
# ---------------------------------------------------
@app.get("/")
def home():
    return {"status": "alive", "message": "Misr Sweets Bot Running"}

# ---------------------------------------------------
# التحقق من Webhook (Facebook Verification Step)
# ---------------------------------------------------
@app.get("/webhook")
def verify(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        logger.info("✔ Webhook Verified Successfully")
        return int(challenge)

    raise HTTPException(status_code=403, detail="Verification failed")

# ---------------------------------------------------
# استقبال الرسائل من Facebook
# ---------------------------------------------------
@app.post("/webhook")
async def webhook(request: Request):

    body = await request.json()
    logger.info(f"📩 Incoming Event: {body}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging_event in entry.get("messaging", []):

                # هل الرسالة تحتوي على نص؟
                if "message" in messaging_event and "text" in messaging_event["message"]:

                    sender_id = messaging_event["sender"]["id"]
                    received_text = messaging_event["message"]["text"]

                    logger.info(f"👤 User: {sender_id} | Message: {received_text}")

                    # توليد الرد باستخدام الذكاء الاصطناعي
                    reply = ai_response(received_text)

                    # إرسال الرد إلى فيسبوك
                    send_message(sender_id, reply)

    return JSONResponse({"status": "ok"}, status_code=200)

# ---------------------------------------------------
# ذكاء DeepSeek — الرد على الرسائل
# ---------------------------------------------------
def ai_response(user_text):

    # قراءة بيانات الشركة من data.txt
    company_data = ""
    if os.path.exists("data.txt"):
        with open("data.txt", "r", encoding="utf-8") as f:
            company_data = f.read()

    prompt = f"""
أنت بوت خدمة عملاء لمحل "حلويات مصر".
هنا بيانات الشركة:

{company_data}

التعليمات:
- الرد يكون ودي ومختصر.
- عدم اختراع معلومات غير موجودة.
- الاعتماد فقط على البيانات المكتوبة فوق.
- الرد باللهجة المصرية.
سؤال العميل: {user_text}
"""

    url = "https://api.deepseek.com/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "أنت مساعد خدمة عملاء محترف."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()

        if "choices" in result:
            reply_text = result["choices"][0]["message"]["content"]
            return reply_text

        logger.error(f"DeepSeek Error Response: {result}")
        return "عذرًا، فيه مشكلة في المعالجة دلوقتي. حاول تاني."

    except Exception as e:
        logger.error(f"DeepSeek Error: {e}")
        return "في مشكلة تقنية دلوقتي — حاول بعد شوية."

# ---------------------------------------------------
# إرسال الرسائل إلى Facebook Messenger
# ---------------------------------------------------
def send_message(user_id, text):

    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={FACEBOOK_PAGE_ACCESS_TOKEN}"

    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, json=payload)
        logger.info(f"📤 Sent: {text[:50]}... | Status: {r.status_code}")

    except Exception as e:
        logger.error(f"FB Send Error: {e}")

# ---------------------------------------------------
# تشغيل السيرفر على Railway
# ---------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
