import os
import logging
import requests
import base64
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "data.txt" # تأكد إن ده نفس اسم الملف عندك
MODEL = "llama-3.1-8b-instant"

app = FastAPI()

# قراءة البيانات
try:
    # محاولة قراءة محلية أولاً
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_BASE = f.read()
    logger.info("✅ Data loaded successfully")
except:
    KNOWLEDGE_BASE = "لا توجد بيانات."

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

# 🟢 دالة التحديث على GitHub (مع كشف الأخطاء)
def update_github_file(new_info):
    if not GITHUB_TOKEN or not REPO_NAME:
        return "⚠️ إعدادات GitHub (Token/Repo) ناقصة في Railway."

    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    try:
        # 1. محاولة جلب الملف
        get_resp = requests.get(url, headers=headers)
        
        if get_resp.status_code == 404:
            return f"❌ خطأ 404: مش لاقي ملف اسمه {FILE_PATH} في المستودع {REPO_NAME}."
        elif get_resp.status_code == 401:
            return "❌ خطأ 401: التوكن غلط أو صلاحيته انتهت."
        elif get_resp.status_code != 200:
            return f"❌ خطأ في القراءة: {get_resp.status_code} - {get_resp.text}"
        
        file_data = get_resp.json()
        sha = file_data['sha']
        
        # 2. التعديل
        old_content = base64.b64decode(file_data['content']).decode('utf-8')
        updated_content = f"{old_content}\n\n=== 🆕 {new_info}"
        encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')

        # 3. الحفظ
        data = {
            "message": f"Bot learned: {new_info}",
            "content": encoded_content,
            "sha": sha
        }
        
        put_resp = requests.put(url, headers=headers, json=data)
        
        if put_resp.status_code == 200:
            return "✅ تم الحفظ في GitHub بنجاح! (انتظر دقيقة للتحديث)."
        else:
            return f"❌ خطأ في الحفظ: {put_resp.status_code} - {put_resp.text}"

    except Exception as e:
        return f"❌ خطأ في الاتصال: {e}"

async def generate_reply(user_id: str, user_msg: str):
    # أمر التعليم
    if user_msg.strip().startswith("اتعلم") or user_msg.strip().startswith("تعلم"):
        info = user_msg.replace("اتعلم", "").replace("تعلم", "").strip()
        if len(info) < 3: return "اكتب المعلومة، مثال: اتعلم كذا كذا"
        return update_github_file(info)

    # الرد الطبيعي
    system_prompt = f"""
    أنت موظف خدمة عملاء لشركة "حلويات مصر".
    معلوماتك:
    {KNOWLEDGE_BASE}
    
    تعليمات:
    1. رد باختصار وود.
    2. لو طلب المنيو ابعت اللينكات.
    3. ابحث في آخر الملف عن التحديثات.
    
    سؤال العميل: {user_msg}
    """

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": system_prompt}],
        "temperature": 0.2,
        "max_tokens": 300
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            else:
                return "معلش ثواني."
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
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={PAGE_TOKEN}"
    payload = {"recipient": {"id": user_id}, "message": {"text": text}}
    requests.post(url, json=payload)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
