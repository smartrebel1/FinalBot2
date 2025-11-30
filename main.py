import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# -----------------------------------------------------
# ⭐ محاولة تحميل Gemini بشكل آمن
# -----------------------------------------------------
USE_GEMINI = False
try:
    import google.generativeai as genai
    USE_GEMINI = True
except Exception as e:
    print("Gemini not available, fallback to simple mode.")

# -----------------------------------------------------
# ⭐ إعداد السجلات
# -----------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------
# ⭐ تحميل المتغيرات
# -----------------------------------------------------
load_dotenv()
VERIFY_TOKEN = os.getenv("my_verify_token_123")
PAGE_TOKEN = os.getenv("EAAc4O5PZCrpoBQPcrJ18mtto24wX01WoDDyvt8VWSIp2YNzdll2NXX3bdrThZBVmRm1H5ghS7JIpqx5tP9iezn6ujjlvqlzp9seAtkA2W1abrW35x2Yt8qBI463XCCfMegZByV9Bo4EF4AJuFHIkvI6mZAUdrzZCIa3I6kAq0g9Wv4E2lX8FQGUdgUwxKjwco7A2jjCeg8OKzMi6aV20PugNibQZDZD")
GEMINI_KEY = os.getenv("AIzaSyCexP81od_dlYoO0oETaVKhLumunSFbJJY")

# لو في مفتاح Gemini → فعّل
if USE_GEMINI and GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("✔ Gemini AI Loaded")
else:
    logger.info("⚠ Gemini Not Available — using Simple Reply Mode")

# -----------------------------------------------------
# ⭐ إنشاء التطبيق
# -----------------------------------------------------
app = FastAPI()

# -----------------------------------------------------
# ⭐ Health Check
# -----------------------------------------------------
@app.get("/")
def home():
    return {"status": "alive"}

# -----------------------------------------------------
# ⭐ Webhook Verify
# -----------------------------------------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)

    raise HTTPException(status_code=403)

# -----------------------------------------------------
# ⭐ استقبال رسائل الفيسبوك
# -----------------------------------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"]:
                    sender = event["sender"]["id"]
                    user_msg = event["message"]["text"]

                    reply = generate_reply(user_msg)
                    send_message(sender, reply)

    return JSONResponse({"status": "ok"})

# -----------------------------------------------------
# ⭐ قراءة data.txt
# -----------------------------------------------------
def load_data():
    if not os.path.exists("data.txt"):
        return ""
    with open("data.txt", "r", encoding="utf-8") as f:
        return f.read()

DATA_TEXT = load_data()

# -----------------------------------------------------
# ⭐ إنشاء الرد (Gemini أو بسيط)
# -----------------------------------------------------
def generate_reply(text):
    # 🤖 لو Gemini شغّال
    if USE_GEMINI and GEMINI_KEY:
        try:
            prompt = f"""
            أنت بوت خدمة عملاء حلويات مصر.
            استخدم هذه المعلومات فقط:

            {DATA_TEXT}

            رسالة العميل: {text}
            """
            result = model.generate_content(prompt)
            return result.text.strip()
        except:
            pass  # لو خطأ استخدم الرد البسيط

    # 💬 رد بسيط باستخدام data.txt
    for line in DATA_TEXT.splitlines():
        if ":" in line:
            key = line.split(":")[0].strip()
            if key in text:
                return line

    return "شكراً لتواصلك! تحت أمرك 😊"

# -----------------------------------------------------
# ⭐ إرسال الرسائل إلى فيسبوك
# -----------------------------------------------------
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}

    try:
        r = requests.post(url, params=params, json=payload)
        if r.status_code != 200:
            logger.error(f"FB Send Error: {r.text}")
    except Exception as e:
        logger.error(f"Exception FB: {e}")

# -----------------------------------------------------
# ⭐ تشغيل السيرفر
# -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
