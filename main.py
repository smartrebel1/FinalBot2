import os
import logging
import requests
import google.generativeai as genai
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# --------------------------
#   إعداد السجلات
# --------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------
#   تحميل متغيرات البيئة
# --------------------------
load_dotenv()
FACEBOOK_VERIFY_TOKEN = os.getenv("my_verify_token_123")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("EAAc4O5PZCrpoBQPcrJ18mtto24wX01WoDDyvt8VWSIp2YNzdll2NXX3bdrThZBVmRm1H5ghS7JIpqx5tP9iezn6ujjlvqlzp9seAtkA2W1abrW35x2Yt8qBI463XCCfMegZByV9Bo4EF4AJuFHIkvI6mZAUdrzZCIa3I6kAq0g9Wv4E2lX8FQGUdgUwxKjwco7A2jjCeg8OKzMi6aV20PugNibQZDZD")
GEMINI_API_KEY = os.getenv("AIzaSyCexP81od_dlYoO0oETaVKhLumunSFbJJY")

# --------------------------
#   إعداد Gemini API
# --------------------------
if GEMINI_API_KEY:
    genai.configure(api_key=AIzaSyCexP81od_dlYoO0oETaVKhLumunSFbJJY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    logger.error("❌ Gemini API Key missing!")

# --------------------------
#   FastAPI
# --------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

# --------------------------
#   Webhook Verification
# --------------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403)

# --------------------------
#   Webhook Listener
# --------------------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for message in entry.get("messaging", []):
                
                if "message" in message and "text" in message["message"]:

                    sender = message["sender"]["id"]
                    user_text = message["message"]["text"]

                    logger.info(f"User: {user_text}")

                    bot_reply = generate_reply(user_text)

                    send_message(sender, bot_reply)

    return JSONResponse({"status": "ok"})

# --------------------------
#   AI Reply (Gemini)
# --------------------------
def generate_reply(user_text):

    # تحميل الداتا
    company_data = ""
    if os.path.exists("data.txt"):
        with open("data.txt", "r", encoding="utf8") as f:
            company_data = f.read()

    prompt = f"""
أنت بوت خدمة عملاء لشركة حلويات مصر.
استخدم المعلومات التالية فقط للإجابة:

{company_data}

إذا لم تجد إجابة في المعلومات → استخدم ذكاءك وقدم رد مفيد ومحترم.

سؤال العميل:
{user_text}
"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(e)
        return "حصل عطل بسيط.. حاول تاني بعد لحظة 💜"

# --------------------------
#   Facebook Send API
# --------------------------
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, params=params, json=payload)
        logger.info(f"FB Response: {r.text}")
    except Exception as e:
        logger.error(f"Send error: {e}")


# --------------------------
#   Run on Railway
# --------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
