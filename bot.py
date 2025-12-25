import os
import logging
import requests
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn
from datetime import datetime
import pytz 

# 1. إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# 2. المتغيرات
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")

FILE_PATH = "data.txt"  
MODEL = "llama-3.1-8b-instant"

# 🛑 قائمة المستخدمين الموقوفين مؤقتاً (عشان الأدمن يرد)
# (دي ذاكرة في الرامات، لو السيرفر رستر هتتمسح، وده طبيعي)
PAUSED_USERS = set()

app = FastAPI()

# 3. قراءة البيانات
try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully")
except:
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

# دالة التحديث على GitHub
def update_github_file(new_info):
    if not GITHUB_TOKEN or not REPO_NAME:
        return "⚠️ إعدادات GitHub ناقصة."
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        get_resp = requests.get(url, headers=headers)
        if get_resp.status_code != 200: return "❌ خطأ في الوصول للملف."
        file_data = get_resp.json()
        sha = file_data['sha']
        old_content = base64.b64decode(file_data['content']).decode('utf-8')
        updated_content = f"{old_content}\n\n=== 🆕 تحديث جديد ===\n- {new_info}"
        encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')
        data = {"message": f"Bot learned: {new_info}", "content": encoded_content, "sha": sha}
        requests.put(url, headers=headers, json=data)
        return "✅ تم الحفظ."
    except Exception as e:
        return f"❌ خطأ: {e}"

# 🟢 المنطق الرئيسي للرد
async def generate_reply(user_id: str, user_msg: str):
    msg = user_msg.strip()

    # 🛑 1. التحكم اليدوي (Stop/Start)
    # لو الأدمن (أو أي حد) كتب "توقف" في الشات، البوت هيسكت لليوزر ده
    if msg.lower() in ["توقف", "stop", "بس", "اسكت"]:
        PAUSED_USERS.add(user_id)
        return "🛑 تم إيقاف البوت لهذا المستخدم. للتشغيل مرة أخرى اكتب 'اشتغل' أو 'start'."
    
    if msg.lower() in ["اشتغل", "start", "رد", "عمل"]:
        if user_id in PAUSED_USERS:
            PAUSED_USERS.remove(user_id)
            return "✅ تم تفعيل البوت مرة أخرى."
        else:
            return "✅ البوت يعمل بالفعل."

    # لو المستخدم في قائمة الإيقاف، البوت مش هيرد خالص (عشان الأدمن يرد)
    if user_id in PAUSED_USERS:
        return None

    # 🛠️ 2. أوامر التعليم (تحديث GitHub)
    if msg.startswith(("#تحديث", "اتعلم")):
        info = msg.replace("#تحديث", "").replace("اتعلم", "").strip()
        return update_github_file(info)

    # 🌙 3. الوضع الليلي الصارم (بدون ذكاء اصطناعي)
    # من 9 مساءً (21) إلى 8 صباحاً (8)
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.now(cairo_tz)
    
    if now.hour >= 21 or now.hour < 8:
        # رد ثابت لا يتغير ولا يروح لـ Groq
        return """أهلاً بك 👋
احنا حالياً خارج مواعيد العمل الرسمية (المواعيد من 8 ص لـ 10 م).
أنا المساعد الآلي، وده المنيو بتاعنا تقدر تطلبه أونلاين:

‏‎📜 منيو الحلويات المصرية: https://photos.app.goo.gl/g9TAxC6JVSDzgiJz5
‏‎📜 منيو الحلويات الشرقية: https://photos.app.goo.gl/vjpdMm5fWB2uEJLR8
‏‎📜 التورت: https://photos.app.goo.gl/SC4yEAHKjpSLZs4z5
📜 كل الكتالوجات: https://misrsweets.com/catalogs/

سيب طلبك وهيتم التواصل معاك في الصباح فوراً 💜"""

    # ☀️ 4. الوضع النهاري (ذكاء اصطناعي مختصر جداً)
    system_prompt = f"""
    أنت نظام رد آلي لـ "حلويات مصر".
    
    البيانات:
    {KNOWLEDGE_BASE}
    
    ⚠️ تعليمات الرد (صارمة جداً):
    1. **الاختصار:** العميل لا يحب الكلام الكثير. جاوب على قد السؤال بالظبط (السعر والنوع).
    2. **بدون مقدمات:** لا تقل (أهلاً بك، يسعدنا، ...) إلا في أول رسالة فقط. ادخل في الموضوع فوراً.
    3. **المنيو:** لو السؤال عن المنيو، انسخ الروابط فقط.
    4. **عدم التوفر:** لو الصنف غير موجود، قل "غير متاح حالياً" فقط.

    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.1, # تجميد الإبداع للالتزام بالنص
        "max_tokens": 200   # تقليل عدد الكلمات
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            return None
        except:
            return None

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    
                    # استدعاء دالة الرد
                    reply = await generate_reply(sender, text)
                    
                    # إرسال الرد فقط لو فيه رد (عشان خاصية الإيقاف)
                    if reply:
                        send_message(sender, reply)
                        
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    if not PAGE_TOKEN: return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
