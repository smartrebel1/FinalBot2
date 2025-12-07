import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx
import uvicorn
import time

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# تحميل المفاتيح
load_dotenv()

# إعدادات الموديل والمفاتيح
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# موديل سريع جداً وذكي
MODEL = "llama-3.3-70b-versatile"

logger.info(f"🚀 BOT RUNNING WITH {MODEL}")

app = FastAPI()

# قراءة البيانات مرة واحدة عند التشغيل لتسريع الأداء
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully from data.txt")
except Exception as e:
    logger.error(f"⚠️ Error loading data.txt: {e}")
    KNOWLEDGE_BASE = "عفواً، لا توجد معلومات متاحة حالياً."

@app.get("/")
def home():
    return {"status": "alive", "model": MODEL}

# التحقق من فيسبوك
@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)

    raise HTTPException(status_code=403, detail="Forbidden")

# دالة الذكاء الاصطناعي (Groq)
async def generate_reply(user_msg: str):
    # تجهيز البرومبت (تعليمات البوت)
    system_prompt = f"""
أنت مساعد خدمة عملاء ذكي ومحترم لشركة "حلويات مصر" (Misr Sweets).
مهمتك هي الرد على العملاء باللهجة المصرية الودودة بناءً *فقط* على البيانات التالية.

=== بيانات الشركة والمنيو ===
{KNOWLEDGE_BASE}
=============================

تعليمات صارمة:
1. لا تؤلف أسعاراً أو منتجات غير موجودة في البيانات.
2. إذا سأل العميل عن "المنيو" بشكل عام، أعطه رابط "جميع الكتالوجات" أو رابط القسم الذي يسأل عنه.
3. كن مختصراً ومفيداً، واستخدم الإيموجي (🍰، 🎂) بشكل مناسب.
4. إذا لم تجد المعلومة، اعتذر وقل: "المعلومة دي مش عندي حالياً، ممكن تتصل بالفرع للتأكد".
5. لطلبات الأوردر، اطلب منهم: الاسم، العنوان، ورقم الهاتف.

سؤال العميل: {user_msg}
"""

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": system_prompt}
        ],
        "temperature": 0.3,  # قليل لتقليل التأليف
        "max_tokens": 300
    }

    # محاولة الاتصال بـ Groq (مع Retry)
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                logger.error(f"🔥 Groq Error ({response.status_code}): {response.text}")
        
        except Exception as e:
            logger.error(f"⚠️ Connection Error Attempt {attempt+1}: {e}")
        
        time.sleep(1) # انتظار ثانية قبل المحاولة التالية

    return "معلش في ضغط على السيرفر دلوقتي، ممكن تبعت تاني؟ ❤️"

# استقبال الرسائل
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                # التأكد أنها رسالة نصية وليست إشعار آخر
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    
                    logger.info(f"👤 User: {text}")

                    # الحصول على الرد وإرساله
                    reply = await generate_reply(text)
                    send_message(sender, reply)

        return JSONResponse({"status": "ok"}, status_code=200)

    return JSONResponse({"status": "ignored"}, status_code=200)

# إرسال الرسالة لفيسبوك
def send_message(user_id, text):
    if not PAGE_TOKEN:
        logger.error("❌ PAGE_TOKEN is missing!")
        return

    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {
        "recipient": {"id": user_id},
        "message": {"text": text}
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            logger.error(f"❌ Facebook Send Error: {r.text}")
    except Exception as e:
        logger.error(f"❌ Connection Error sending to FB: {e}")

# تشغيل السيرفر
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
