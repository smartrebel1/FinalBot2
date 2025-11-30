import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# -----------------------------------------------------
# ⭐ محاولة تحميل Gemini
# -----------------------------------------------------
USE_GEMINI = False
try:
    import google.generativeai as genai
    USE_GEMINI = True
except:
    print("⚠ Gemini not installed — Simple mode only")

# -----------------------------------------------------
# ⭐ Logs
# -----------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------------------------------
# ⭐ Load Environment Variables
# -----------------------------------------------------
load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if USE_GEMINI and GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("✔ Gemini Enabled")
else:
    logger.info("⚠ Gemini Not Available — Simple Reply Only")

# -----------------------------------------------------
# ⭐ FastAPI App
# -----------------------------------------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

# -----------------------------------------------------
# ⭐ Webhook Verify (GET)
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
# ⭐ Webhook (POST)
# -----------------------------------------------------
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):

                if "message" in event and "text" in event["message"]:
                    sender = event["sender"]["id"]
                    msg = event["message"]["text"]

                    reply = generate_reply(msg)
                    send_message(sender, reply)

    return JSONResponse({"status": "ok"})

# -----------------------------------------------------
# ⭐ Load Data
# -----------------------------------------------------
def load_data():
    if os.path.exists("data.txt"):
        return open("data.txt", "r", encoding="utf-8").read()
    return ""

DATA_TEXT = load_data()

# -----------------------------------------------------
# ⭐ Reply Generator
# -----------------------------------------------------
def generate_reply(text):

    # Gemini AI
    if USE_GEMINI and GEMINI_KEY:
        try:
            prompt = f"""
            أنت بوت دعم عملاء حلويات مصر.
            استخدم المعلومات التالية فقط للرد:

            {DATA_TEXT}

            رسالة العميل: {text}
            """

            out = model.generate_content(prompt)
            return out.text.strip()
        except Exception as e:
            logger.error(f"Gemini Error: {e}")

    # Simple reply
    for line in DATA_TEXT.splitlines():
        if ":" in line:
            key = line.split(":")[0].strip()
            if key in text:
                return line

    return "شكراً لتواصلك! تحت أمرك 😊"

# -----------------------------------------------------
# ⭐ Send Message
# -----------------------------------------------------
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}

    r = requests.post(url, params=params, json=payload)

    if r.status_code != 200:
        logger.error(f"Facebook Error: {r.text}")

# -----------------------------------------------------
# ⭐ Run Server
# -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
