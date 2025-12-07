import os
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn

# 1. إعداد السجلات لمراقبة البوت
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# 2. تحميل المتغيرات من Railway
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# 🔥 التغيير المهم: استخدام موديل سريع جداً لتجنب التوقف
MODEL = "llama-3.1-8b-instant"

app = FastAPI()

# 3. إعداد الذاكرة
# ذاكرة المحادثة الحالية (مؤقتة)
conversations = {}
# ملف الذاكرة الدائمة (للتعليم)
MEMORY_FILE = "memory.txt"

# 4. قراءة ملف البيانات الأساسي
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        BASE_KNOWLEDGE = f.read()
    logger.info("✅ Data loaded successfully")
except Exception as e:
    BASE_KNOWLEDGE = "لا توجد بيانات أساسية."

# دالة لدمج الداتا الأصلية مع المعلومات الجديدة التي تعلمها
def get_full_knowledge():
    memory_content = ""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memory_content = f.read()
    
    return f"""
    {BASE_KNOWLEDGE}
    
    === 🧠 معلومات جديدة تم تعلمها (تحديثات) ===
    {memory_content}
    """

# دالة حفظ معلومة جديدة
def learn_new_info(info):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n- {info}")
    return "تمام يا ريس، حفظت المعلومة دي في ذاكرتي! 🧠✅"

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
    # --- أولاً: فحص أوامر التعليم ---
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        new_info = user_msg.replace("اتعلم", "").replace("تعلم", "").strip()
        if len(new_info) > 3:
            return learn_new_info(new_info)
        else:
            return "عشان اتعلم، اكتب المعلومة بعد الكلمة، مثال: اتعلم ان التوصيل مجاني."

    # --- ثانياً: تجهيز الرد الذكي ---
    
    # استرجاع سياق المحادثة (الذاكرة القصيرة)
    history = conversations.get(user_id, [])
    chat_context = ""
    for msg in history[-3:]: 
        chat_context += f"- {msg['role']}: {msg['content']}\n"
    
    # قراءة كل البيانات (القديمة + الجديدة)
    current_knowledge = get_full_knowledge()

    # تعليمات البوت (System Prompt)
    system_prompt = f"""
    أنت موظف مبيعات ذكي ومحترف لشركة "حلويات مصر" (Misr Sweets).
    
    البيانات المتاحة (الأسعار والأنواع):
    === DATA ===
    {current_knowledge}
    ============

    ⚠️ تعليمات صارمة للرد:
    1. **اللهجة:** مصرية ودودة ومحترمة.
    2. **البحث:** ابحث في البيانات بدقة. المعلومات في قسم "تحديثات" لها الأولوية وتلغي القديم.
    3. **الشمول:** لو العميل سأل عن صنف عام (مثل "كنافة")، اعرض له القائمة المتاحة بأسعارها.
    4. **عدم التوفر:** لو المنتج غير موجود أو الاسم غريب، قل:
       "للأسف المنتج ده مش واضح عندي دلوقتي، لكن دي المنيو الكاملة 👇"
       (وانسخ قسم روابط المنيو والكتالوجات من البيانات).
    
    سياق المحادثة السابقة:
    {chat_context}
    
    سؤال العميل الحالي: {user_msg}
    الرد:
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.3, # درجة إبداع قليلة للالتزام بالحقائق
        "max_tokens": 400
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                reply_text = response.json()["choices"][0]["message"]["content"].strip()
                
                # تحديث الذاكرة القصيرة
                history.append({"role": "User", "content": user_msg})
                history.append({"role": "Bot", "content": reply_text})
                conversations[user_id] = history[-10:] # نحتفظ بآخر 10 رسائل
                
                return reply_text
            elif response.status_code == 429:
                return "معلش في ضغط كبير ع السيستم، ثواني وجرب تاني! 🙏"
            else:
                logger.error(f"Groq Error: {response.text}")
                return "عطل فني بسيط، جرب كمان شوية."
        except Exception as e:
            logger.error(f"Connection Error: {e}")
            return "النظام مشغول حالياً."

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    # الرد
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    if not PAGE_TOKEN:
        return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"FB Send Error: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
