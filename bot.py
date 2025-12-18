import os
import logging
import requests
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import uvicorn

# 1. إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

# 2. تحميل المتغيرات من Railway
VERIFY_TOKEN = os.getenv("FACEBOOK_VERIFY_TOKEN")
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # مفتاح جيت هاب
REPO_NAME = os.getenv("REPO_NAME")        # اسم المستودع (user/repo)

# الموديل السريع
MODEL = "llama-3.1-8b-instant"
# اسم ملف الداتا اللي هنعدله
FILE_PATH = "data.txt"

app = FastAPI()

# 3. قراءة البيانات الحالية عند التشغيل
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

# 🟢 4. الدالة السحرية: التحديث المباشر على GitHub
def update_github_file(new_info):
    if not GITHUB_TOKEN or not REPO_NAME:
        return "⚠️ فيه مشكلة في إعدادات GitHub في Railway. تأكد من المتغيرات."

    # رابط API الخاص بملف الداتا
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    try:
        # أ) نجيب الملف الحالي عشان ناخد الـ SHA (بصمة الملف)
        get_resp = requests.get(url, headers=headers)
        if get_resp.status_code != 200:
            return "❌ مش عارف أوصل لملف الداتا على GitHub."
        
        file_data = get_resp.json()
        sha = file_data['sha']
        
        # ب) نفك تشفير المحتوى القديم ونضيف عليه الجديد
        old_content = base64.b64decode(file_data['content']).decode('utf-8')
        
        # بنضيف المعلومة الجديدة في آخر الملف بتاريخ اليوم
        updated_content = f"{old_content}\n\n=== 🆕 تحديث جديد ===\n- {new_info}"
        
        # ج) نشفر المحتوى الجديد (Base64) عشان GitHub بيفهم كده
        encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')

        # د) نبعت التحديث (Push/Commit)
        data = {
            "message": f"Bot learned: {new_info}", # رسالة الـ commit
            "content": encoded_content,
            "sha": sha
        }
        
        put_resp = requests.put(url, headers=headers, json=data)
        
        if put_resp.status_code == 200:
            return "✅ تمام يا ريس! عدلت ملف الداتا بنفسي على GitHub.\n(البوت هيعمل ريستارت دقيقة واحدة عشان يحدث معلوماته ويرجعلك)."
        else:
            return f"❌ حصل خطأ وأنا بحدث الملف: {put_resp.status_code}"

    except Exception as e:
        return f"❌ خطأ في الاتصال: {e}"

# 5. منطق الرد والذكاء الاصطناعي
async def generate_reply(user_id: str, user_msg: str):
    
    # -- فحص أمر التعليم --
    # لو الرسالة بتبدأ بـ "اتعلم" أو "تعلم"
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        # استخراج المعلومة (حذف كلمة اتعلم)
        info_to_learn = user_msg.replace("اتعلم", "").replace("تعلم", "").strip()
        
        if len(info_to_learn) < 3:
            return "اكتب المعلومة بعد كلمة 'اتعلم'، مثال: اتعلم ان التوصيل مجاني."
            
        # استدعاء دالة تحديث GitHub
        return update_github_file(info_to_learn)

    # -- الرد الطبيعي --
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر" (Misr Sweets).
    
    مرجعك الوحيد للمعلومات:
    === DATA ===
    {KNOWLEDGE_BASE}
    ============

    ⚠️ تعليمات صارمة للرد:
    1. **المجاملات:** لو العميل قال (شكراً، تسلم، هاي)، رد بترحيب وذوق فوراً ولا تبحث في الأسعار.
    2. **المنيو:** لو العميل طلب "المنيو"، انسخ قسم "روابط المنيو والكتالوجات" فقط.
    3. **التوصيل:** التزم بنص التوصيل الموجود في الداتا.
    4. **التحديثات:** ابحث في آخر الملف عن أي تحديثات جديدة لأنها الأهم.
    5. **عدم المعرفة:** لو المعلومة مش موجودة، قول: "للأسف المعلومة دي مش واضحة قدامي دلوقتي".

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
            else:
                return "معلش ثواني وراجعلك (ضغط شبكة) 💜"
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
    requests.post(url, json=payload)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
