import os
import logging
import requests
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn
from datetime import datetime
import pytz # مكتبة التوقيت

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

app = FastAPI()

# 3. قراءة البيانات
try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully")
except:
    KNOWLEDGE_BASE = "لا توجد بيانات متاحة حالياً."

@app.get("/")
def home():
    return {"status": "alive", "repo": REPO_NAME}

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
        return "✅ تمام يا ريس! تم حفظ المعلومة."
    except Exception as e:
        return f"❌ خطأ: {e}"

# 🟢 دالة التحقق من الوقت (وضع الليل)
def get_time_instructions():
    # تحديد توقيت مصر
    cairo_tz = pytz.timezone('Africa/Cairo')
    now = datetime.now(cairo_tz)
    current_hour = now.hour

    # لو الساعة أكبر من أو تساوي 21 (9 مساءً) أو أقل من 8 (8 صباحاً)
    if current_hour >= 21 or current_hour < 8:
        return """
        🚨 **تنبيه هام جداً (الوضع الليلي):**
        الوقت الآن متأخر (خارج مواعيد العمل الرسمية).
        يجب أن تضيف هذه الفقرة في بداية ردك مهما كان السؤال:
        "أهلاً بك 👋، نحن الآن خارج مواعيد العمل الرسمية. أنا المساعد الآلي موجود للرد على استفساراتك، ولطلب أوردر يرجى ترك تفاصيلك وسيقوم أحد ممثلي خدمة العملاء بالرد عليك في الصباح 💜."
        
        ثم جاوب على سؤاله (السعر أو التفاصيل) بشكل طبيعي، واختم الرسالة بروابط المنيو دائماً.
        """
    return "" # لو في وقت العمل العادي، مفيش تعليمات إضافية

async def generate_reply(user_id: str, user_msg: str):
    # أمر التعليم
    if user_msg.strip().startswith(("#تحديث", "اتعلم")):
        info = user_msg.replace("#تحديث", "").replace("اتعلم", "").strip()
        return update_github_file(info)

    # جلب تعليمات الوقت
    time_instruction = get_time_instructions()

    # الرد الطبيعي
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر".
    
    البيانات:
    {KNOWLEDGE_BASE}
    
    {time_instruction}

    تعليمات عامة:
    1. خليك ودود ومختصر.
    2. لو العميل طلب المنيو ابعت اللينكات.
    3. لو الوقت متأخر (حسب تنبيه الوضع الليلي بالأعلى)، نفذ التعليمات المذكورة هناك بدقة.

    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.3,
        "max_tokens": 450
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                return "معلش ثواني وراجعلك."
        except:
            return "النظام مشغول."

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for msg in entry.get("messaging", []):
                if "message" in msg and "text" in msg["message"]:
                    sender = msg["sender"]["id"]
                    text = msg["message"]["text"]
                    reply = await generate_reply(sender, text)
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
