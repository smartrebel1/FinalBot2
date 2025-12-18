import os
import logging
import requests
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn

# 1. إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# 2. تحميل المتغيرات من Railway
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")

# إعدادات الملف والموديل
FILE_PATH = "data.txt"  # اسم ملف الداتا في جيت هاب
MODEL = "llama-3.1-8b-instant"

app = FastAPI()

# 3. قراءة البيانات محلياً عند بدء التشغيل
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

# 🟢 4. دالة التحديث المباشر على GitHub (مع كشف الأخطاء)
def update_github_file(new_info):
    # طباعة معلومات المتغيرات في اللوج للمساعدة في الحل
    logger.info(f"🔍 DEBUG CHECK: REPO_NAME = '{REPO_NAME}'")
    logger.info(f"🔍 DEBUG CHECK: TOKEN Length = {len(GITHUB_TOKEN) if GITHUB_TOKEN else 0}")

    # التحقق من وجود المتغيرات
    if not GITHUB_TOKEN or not REPO_NAME:
        return f"⚠️ إعدادات GitHub ناقصة في Railway.\nRepo: {REPO_NAME}\nToken: {'موجود' if GITHUB_TOKEN else 'غير موجود'}"

    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    try:
        # أ) جلب الملف الحالي (للحصول على SHA)
        get_resp = requests.get(url, headers=headers)
        
        if get_resp.status_code == 404:
            return f"❌ خطأ 404: مش لاقي ملف اسمه {FILE_PATH} في {REPO_NAME}.\nتأكد إن اسم الملف في GitHub مطابق للكود."
        elif get_resp.status_code == 401:
            return "❌ خطأ 401: التوكن (Token) غلط أو منتهي الصلاحية."
        elif get_resp.status_code != 200:
            return f"❌ خطأ في قراءة GitHub: {get_resp.status_code} - {get_resp.text}"
        
        file_data = get_resp.json()
        sha = file_data['sha']
        
        # ب) فك التشفير وإضافة المعلومة
        old_content = base64.b64decode(file_data['content']).decode('utf-8')
        updated_content = f"{old_content}\n\n=== 🆕 تحديث جديد ===\n- {new_info}"
        
        # ج) التشفير مرة أخرى
        encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')

        # د) إرسال التحديث (Commit)
        data = {
            "message": f"Bot learned: {new_info}", 
            "content": encoded_content,
            "sha": sha
        }
        
        put_resp = requests.put(url, headers=headers, json=data)
        
        if put_resp.status_code == 200:
            return "✅ تمام يا ريس! عدلت ملف الداتا بنفسي على GitHub.\n(البوت هيعمل ريستارت دقيقة واحدة عشان يحدث معلوماته)."
        elif put_resp.status_code == 403:
            return "❌ خطأ 403: التوكن ده (Read-only) ملوش صلاحية الكتابة. لازم تعمل توكن جديد وتعلم على 'repo'."
        else:
            return f"❌ خطأ في الحفظ: {put_resp.status_code} - {put_resp.text}"

    except Exception as e:
        return f"❌ خطأ في الاتصال: {e}"

# 5. منطق الرد والذكاء
async def generate_reply(user_id: str, user_msg: str):
    # أمر التعليم
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        info = user_msg.replace("اتعلم", "").replace("تعلم", "").strip()
        if len(info) < 2: return "اكتب المعلومة بعد كلمة اتعلم."
        return update_github_file(info)

    # الرد الطبيعي
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر".
    
    مرجعك للمعلومات:
    === DATA ===
    {KNOWLEDGE_BASE}
    ============

    تعليمات الرد:
    1. **المجاملات:** رد بترحيب وذوق فوراً (أهلاً بك يا فندم 💜).
    2. **المنيو:** لو طلب المنيو، انسخ قسم "روابط المنيو" فقط.
    3. **التوصيل:** التزم بنص التوصيل الموجود في الداتا.
    4. **التحديثات:** ابحث في آخر الملف عن أي تحديثات جديدة لأنها الأهم.
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
                return "معلش ثواني وراجعلك (عطل فني بسيط) 💜"
        except:
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
