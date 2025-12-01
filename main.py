import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import uvicorn

# ---------------------- LOGGING ----------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------- LOAD ENV ----------------------
load_dotenv()

VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not VERIFY_TOKEN:
    logger.error("❌ FACEBOOK_VERIFY_TOKEN not found!")
if not PAGE_TOKEN:
    logger.error("❌ FACEBOOK_PAGE_ACCESS_TOKEN not found!")
if not OPENAI_KEY:
    logger.warning("⚠ No OPENAI_API_KEY — replies will be simple only")

# ---------------------- OPENAI (ChatGPT) ----------------------
use_chatgpt = False
openai_client = None

try:
    from openai import OpenAI
    openai_client = OpenAI(api_key=OPENAI_KEY)
    use_chatgpt = True
    logger.info("✔ ChatGPT API enabled")
except Exception as e:
    logger.error(f"❌ ChatGPT import failed: {e}")

# ---------------------- LOAD DATA ----------------------
def load_data():
    if os.path.exists("data.txt"):
        return open("data.txt", "r", encoding="utf-8").read()
    return ""

DATA = load_data()

# ---------------------- FASTAPI APP ----------------------
app = FastAPI()

@app.get("/")
def home():
    return {"status": "alive"}

# ---------------------- VERIFY WEBHOOK ----------------------
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)

    raise HTTPException(status_code=403, detail="Forbidden")

# ---------------------- RECEIVE MESSAGES ----------------------
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

# ---------------------- CREATE REPLY ----------------------
def generate_reply(text):

    # ====== ChatGPT MODE ======
    if use_chatgpt and OPENAI_KEY:
        try:
            prompt = f"""
            أنت بوت خدمة عملاء "حلويات مصر".
            استخدم المعلومات التالية للرد بشكل دقيق:

            {DATA}

            رسالة العميل: {text}

            الرد يجب أن يكون عربي بسيط وواضح.
            """

            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}]
            )

            answer = response.choices[0].message.content
            return answer.strip()

        except Exception as e:
            logger.error(f"ChatGPT Error: {e}")

    # ====== SIMPLE MODE ======
    try:
        for line in DATA.splitlines():
            if ":" in line:
                key = line.split(":")[0].strip()
                if key and key in text:
                    return line
    except:
        pass

    return "شكراً لتواصلك! تحت أمرك 😊"

# ---------------------- SEND MESSAGE TO FACEBOOK ----------------------
def send_message(user_id, text):
    url = "https://graph.facebook.com/v19.0/me/messages"
    params = {"access_token": PAGE_TOKEN}
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    r = requests.post(url, params=params, json=payload)
    if r.status_code != 200:
        logger.error(f"Facebook Error: {r.text}")

# ---------------------- RUN SERVER ----------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(app, host="0.0.0.0", port=port)
