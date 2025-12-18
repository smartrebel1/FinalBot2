import os
import logging
import requests
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn

# 1. إعداد السجلات (عشان نشوف البوت بيعمل إيه في الخلفية)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# 2. تحميل المتغيرات من Railway
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")

# إعدادات الملف والموديل
FILE_PATH = "data.txt"  
MODEL = "llama-3.1-8b-instant"

app = FastAPI()

# 3. قراءة البيانات محلياً عند التشغيل
try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully from local file")
except:
    KNOWLEDGE_BASE = "لا توجد بيانات متاحة حالياً."

@app.get("/")
def home():
    return {"status": "alive", "repo": REPO_NAME, "model": MODEL}

@app.get("/webhook")
def verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)
    raise HTTPException(status_code=403)

# 🟢 4. دالة التحديث المباشر على GitHub
def update_github_file(new_info):
    # طباعة في اللوج للتأكد من المتغيرات
    logger.info(f"🔍 Checking GitHub Vars: Repo={REPO_NAME}, Token_Len={len(str(GITHUB_TOKEN))}")

    if not GITHUB_TOKEN or not REPO_NAME:
        return "⚠️ إعدادات GitHub (Token/Repo) ناقصة في Railway."

    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    try:
        # أ) جلب الملف الحالي
        get_resp = requests.get(url, headers=headers)
        
        if get_resp.status_code == 404:
            return f"❌ خطأ 404: الملف {FILE_PATH} غير موجود في {REPO_NAME}."
        elif get_resp.status_code == 403:
            return "❌ خطأ 403: التوكن لا يملك صلاحية التعديل (Repo Scope Missing)."
        elif get_resp.status_code == 401:
            return "❌ خطأ 401: التوكن غير صحيح."
        
        file_data = get_resp.json()
        sha = file_data['sha']
        
        # ب) فك التشفير وإضافة التحديث
        old_content = base64.b64decode(file_data['content']).decode('utf-8')
        updated_content = f"{old_content}\n\n=== 🆕 تحديث جديد ===\n- {new_info}"
        
        # ج) التشفير مرة أخرى
        encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')

        # د) إرسال التحديث
        data = {
            "message": f"Bot learned: {new_info}", 
            "content": encoded_content,
            "sha": sha
        }
        
        put_resp = requests.put(url, headers=headers, json=data)
        
        if put_resp.status_code == 200:
            return "✅ تمام يا ريس! تم حفظ المعلومة في GitHub.\n(سيتم إعادة تشغيل البوت تلقائياً لتحديث البيانات)."
        else:
            return f"❌ فشل الحفظ: {put_resp.status_code} - {put_resp.text}"

    except Exception as e:
        return f"❌ خطأ في الاتصال: {e}"

# 5. منطق الرد والذكاء
async def generate_reply(user_id: str, user_msg: str):
    # تنظيف النص
    msg = user_msg.strip()
    logger.info(f"📩 Received Message: '{msg}'")

    # --- الكشف عن أوامر التعليم ---
    # يقبل: #تحديث، #learn، اتعلم، تعلم
    triggers = ["#تحديث", "#learn", "اتعلم", "تعلم"]
    
    if any(msg.startswith(t) for t in triggers):
        # استخراج المعلومة بحذف كلمة الأمر
        info = msg
        for t in triggers:
            info = info.replace(t, "")
        
        info = info.strip()
        logger.info(f"⚙️ Learning Triggered. Content: {info}")

        if len(info) < 2: 
            return "اكتب المعلومة بعد الأمر. مثال: #تحديث السعر زاد."
            
        return update_github_file(info)

    # --- الرد الطبيعي (Groq AI) ---
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر" (Misr Sweets).
    
    مرجعك للمعلومات:
    === DATA ===
    {KNOWLEDGE_BASE}
    ============

    تعليمات صارمة:
    1. **المجاملات:** رد بترحيب وذوق فوراً (أهلاً بك يا فندم 💜).
    2. **المنيو:** لو طلب المنيو، انسخ قسم "روابط المنيو والكتالوجات" فقط.
    3. **التوصيل:** التزم بنص التوصيل الموجود في الداتا.
    4. **التحديثات:** انظر في آخر الملف عن أي تحديثات جديدة.
    5. **عدم المعرفة:** لو المعلومة مش موجودة، قول: "للأسف المعلومة دي مش واضحة حالياً".

    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.2,
        "max_tokens": 350
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            elif response.status_code == 429:
                return "معلش في ضغط على السيرفر، ثواني وجرب تاني."
            else:
                logger.error(f"Groq Error: {response.text}")
                return "معلش ثواني وراجعلك (عطل فني بسيط) 💜"
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
                    reply = await generate_reply(sender, text)
                    send_message(sender, reply)
        return JSONResponse({"status": "ok"}, status_code=200)
    return JSONResponse({"status": "ignored"}, status_code=200)

def send_message(user_id, text):
    if not PAGE_TOKEN:
        return
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
