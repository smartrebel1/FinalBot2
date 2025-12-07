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

# 🧠 الذاكرة الدائمة (ملف نحفظ فيه التعليمات الجديدة)
MEMORY_FILE = "memory.txt"

# قراءة البيانات الأساسية
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        BASE_KNOWLEDGE = f.read()
    logger.info("✅ Base Data loaded")
except:
    BASE_KNOWLEDGE = "لا توجد بيانات أساسية."

# دالة قراءة الذاكرة المحدثة
def get_updated_knowledge():
    # نقرأ الداتا الأساسية + أي حاجة اتعلمها جديد في memory.txt
    current_memory = ""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            current_memory = f.read()
    
    return f"""
    {BASE_KNOWLEDGE}
    
    === 🆕 تحديثات ومعلومات جديدة تعلمتها (لها الأولوية) ===
    {current_memory}
    """

# دالة التعليم (تكتب في الملف)
def learn_new_info(info):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n- {info}")
    return "تمام، حفظت المعلومة دي في ذاكرتي! 🧠✅"

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
    # 🔴 1. فحص هل ده أمر تعليم؟ (للأدمن فقط)
    # لو الرسالة بتبدأ بكلمة "اتعلم"
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        new_info = user_msg.replace("اتعلم", "").replace("تعلم", "").strip()
        if len(new_info) > 3:
            return learn_new_info(new_info)
        else:
            return "اكتب المعلومة بعد كلمة 'اتعلم'، مثال: اتعلم ان سعر الكنافة 50"

    # 🔴 2. الرد الطبيعي باستخدام البيانات المحدثة
    full_knowledge = get_updated_knowledge()
    
    system_prompt = f"""
    أنت موظف مبيعات ذكي لشركة "حلويات مصر".
    
    مصدر معلوماتك (الأسعار والأنواع):
    === DATA ===
    {full_knowledge}
    ============

    تعليمات صارمة:
    1. ابحث في قسم "تحديثات جديدة" أولاً، لأنها تلغي الأسعار القديمة.
    2. لو العميل سأل عن صنف (مثل "كنافة")، اعرض كل الأنواع المتاحة وأسعارها.
    3. خليك مختصر ومفيد.
    4. لو المعلومة مش موجودة قول: "المعلومة دي مش عندي حالياً".

    سؤال العميل: {user_msg}
    """

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
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                return "معلش في عطل فني بسيط."
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
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)
