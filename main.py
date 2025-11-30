import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# -------------------------------------------------
# 1) إعداد اللوجز
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# 2) تحميل المتغيرات
# -------------------------------------------------
load_dotenv()

FACEBOOK_VERIFY_TOKEN = os.getenv("my_verify_token_123")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("EAAc4O5PZCrpoBQPcrJ18mtto24wX01WoDDyvt8VWSIp2YNzdll2NXX3bdrThZBVmRm1H5ghS7JIpqx5tP9iezn6ujjlvqlzp9seAtkA2W1abrW35x2Yt8qBI463XCCfMegZByV9Bo4EF4AJuFHIkvI6mZAUdrzZCIa3I6kAq0g9Wv4E2lX8FQGUdgUwxKjwco7A2jjCeg8OKzMi6aV20PugNibQZDZD")
GEMINI_API_KEY = os.getenv("AIzaSyCexP81od_dlYoO0oETaVKhLumunSFbJJY")

# -------------------------------------------------
# 3) محاولة تحميل Gemini – اختياري
# -------------------------------------------------
use_gemini = False
model = None

if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        use_gemini = True
        logger.info("✔ Gemini model loaded successfully")
    except Exception as e:
        logger.error(f"❌ Gemini load failed: {e}")
else:
    logger.warning("⚠ No GEMINI_API_KEY found — fallback to Simple AI")

# -------------------------------------------------
# 4) إنشاء التطبيق
# -------------------------------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

# -------------------------------------------------
# 5) Webhook Verification
# -------------------------------------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == FACEBOOK_VERIFY_TOKEN:
        return int(challenge)

    raise HTTPException(status_code=403, detail="Forbidden")


# -------------------------------------------------
# 6) استقبال الرسائل من فيسبوك
# -------------------------------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                if "message" in event and "text" in event["message"]:

                    sender = event["sender"]["id"]
                    user_msg = event["message"]["text"]

                    # توليد الرد
                    reply = generate_reply(user_msg)

                    # إرسال الرد لفيسبوك
                    send_message(sender, reply)

    return JSONResponse({"status": "ok"})


# -------------------------------------------------
# 7) دالة الرد الذكي
# -------------------------------------------------
def generate_reply(user_text):

    # --- 1) لو Gemini موجود – استخدمه ---
    if use_gemini and model:
        try:
            data = ""
            if os.path.exists("data.txt"):
                data = open("data.txt", encoding="utf8").read()

            prompt = f"""
            أنت بوت خدمة عملاء حلويات مصر.
            استخدم المعلومات التالية للرد على أسئلة العملاء:

            {data}

            السؤال: {user_text}
            اجعل الرد مختصر وواضح وباللهجة المصرية.
            """

            response = model.generate_content(prompt)
            return response.text.strip()

        except Exception as e:
            logger.error(f"Gemini error: {e}")

    # --- 2) لو Gemini مش موجود – Simple AI ---
    if os.path.exists("data.txt"):
        try:
            data_lines = open("data.txt", encoding="utf8").read().splitlines()

            # بحث بسيط في الكلمات
            for line in data_lines:
                key = line.split(":")[0].strip()
                if key and key.lower() in user_text.lower():
                    return line
        except:
            pass

    return "شكراً لتواصلك! فريق حلويات مصر هيساعدك حالاً 💜"


# -------------------------------------------------
# 8) إرسال الرسالة لفيسبوك
# -------------------------------------------------
def send_message(user_id, text):

    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": FACEBOOK_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, params=params, json=payload)
        if r.status_code != 200:
            logger.error(f"Error sending message: {r.text}")
    except Exception as e:
        logger.error(f"Send message failed: {e}")


# -------------------------------------------------
# 9) تشغيل السيرفر (Railway يستخدم PORT)
# -------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    logger.info(f"🚀 Bot running on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
