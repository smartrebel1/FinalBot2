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
MODEL = "llama-3.1-8b-instant"

app = FastAPI()

# ذاكرة المحادثات (تخزين آخر 5 رسائل لكل مستخدم)
conversations = {}
MEMORY_FILE = "memory.txt"

# قراءة البيانات
try:
    with open("data.txt", "r", encoding="utf-8") as f:
        BASE_KNOWLEDGE = f.read()
    logger.info("✅ Data loaded successfully")
except Exception as e:
    BASE_KNOWLEDGE = "لا توجد بيانات."

def get_full_knowledge():
    memory_content = ""
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            memory_content = f.read()
    return f"{BASE_KNOWLEDGE}\n=== معلومات جديدة تم تعلمها ===\n{memory_content}"

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
    # 1. أوامر التعليم
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        return learn_new_info(user_msg.replace("اتعلم", "").replace("تعلم", "").strip())

    # 2. الذاكرة
    history = conversations.get(user_id, [])
    chat_context = ""
    for msg in history[-3:]: 
        chat_context += f"- {msg['role']}: {msg['content']}\n"
    
    full_knowledge = get_full_knowledge()

    # 3. تعليمات البوت الذكية
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر".
    
    مرجعك الوحيد للمعلومات:
    === DATA ===
    {full_knowledge}
    ============

    ⚠️ قواعد الرد (صارمة جداً):
    1. **المجاملات:** لو العميل قال (شكراً، تسلم، هاي، سلام عليكم)، رد بترحيب وذوق فوراً (مثلاً: "يا هلا بيك يا فندم 💜" أو "الشكر لله، تحت أمرك في أي وقت 💜") ولا تبحث في الأسعار.
    2. **المنيو:** لو العميل سأل عن "المنيو" أو "القائمة"، انسخ قسم "روابط المنيو والكتالوجات" من البيانات كما هو بالضبط دون تغيير.
    3. **التوصيل:** التزم بنص التوصيل الموجود في الداتا (طنطا غير متاح حالياً).
    4. **الاختصار:** الإجابة تكون قصيرة ومباشرة، استخدم إيموجي (💜، 🍰).
    5. **عدم المعرفة:** لو المعلومة مش في الداتا، قول: "للأسف المعلومة دي مش واضحة قدامي دلوقتي، ممكن تتواصل مع الفرع للتأكيد".

    سياق سابق:
    {chat_context}
    
    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.2, # تقليل الإبداع للالتزام بالنص
        "max_tokens": 350
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                reply_text = response.json()["choices"][0]["message"]["content"].strip()
                
                # تحديث الذاكرة
                history.append({"role": "User", "content": user_msg})
                history.append({"role": "Bot", "content": reply_text})
                conversations[user_id] = history[-10:]
                
                return reply_text
            else:
                return "معلش ثواني وراجعلك (ضغط شبكة) 💜"
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
