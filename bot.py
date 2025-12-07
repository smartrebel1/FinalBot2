import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# المتغيرات
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = "llama-3.3-70b-versatile"

app = FastAPI()

# --- ذاكرة مؤقتة للمحادثات ---
# تخزين آخر 5 رسائل لكل مستخدم ليفهم السياق
conversations = {}

# قراءة البيانات
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully")
except Exception as e:
    KNOWLEDGE_BASE = "لا توجد بيانات."

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

async def generate_reply(user_id: str, user_msg: str):
    # 1. استرجاع تاريخ المحادثة
    history = conversations.get(user_id, [])
    
    # 2. تجهيز سياق المحادثة (آخر رسالتين + الرسالة الحالية)
    chat_context = ""
    for msg in history[-4:]: # نأخذ آخر 4 تبادلات
        chat_context += f"- {msg['role']}: {msg['content']}\n"
    
    # 3. تعليمات البوت الذكية
    system_prompt = f"""
    أنت موظف مبيعات ذكي ومحترف لشركة "حلويات مصر".
    
    معلوماتك (الأسعار والأنواع) موجودة هنا:
    === BEGIN DATA ===
    {KNOWLEDGE_BASE}
    === END DATA ===

    تعليماتك:
    1. **البحث الشامل:** لو العميل سأل عن صنف عام (مثل "كنافة" أو "تورت"), اجلب له *كل* الأنواع المتاحة وأسعارها في قائمة مرتبة.
    2. **الذاكرة:** افهم سياق الكلام. لو العميل قال "طب وبكام الوسط؟" أو "عايز منها 2"، اعرف هو بيتكلم عن ايه من الرسائل السابقة.
    3. **اللهجة:** اتكلم مصري ودود ومختصر.
    4. **عدم التأليف:** لو المعلومة مش موجودة في الـ DATA، قول "للأسف مش متاح حالياً".

    تاريخ المحادثة السابقة:
    {chat_context}
    
    الرسالة الجديدة من العميل: {user_msg}
    الرد:
    """

    # 4. الاتصال بـ Groq
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.3
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                reply_text = response.json()["choices"][0]["message"]["content"].strip()
                
                # تحديث الذاكرة
                history.append({"role": "User", "content": user_msg})
                history.append({"role": "Bot", "content": reply_text})
                conversations[user_id] = history[-10:] # نحتفظ بآخر 10 رسائل فقط لعدم امتلاء الذاكرة
                
                return reply_text
            else:
                return "معلش في عطل فني بسيط، ثواني وراجعلك."
        except:
            return "النظام مشغول حالياً."

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body["entry"]:
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    # توليد الرد مع الذاكرة
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)
