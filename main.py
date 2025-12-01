import os
import requests
import json

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")

def ai_response(user_text):
    """توليد رد ذكي باستخدام DeepSeek API"""

    # تحميل بيانات الشركة من data.txt
    data = ""
    if os.path.exists("data.txt"):
        with open("data.txt", "r", encoding="utf-8") as f:
            data = f.read()

    # إنشاء الـ Prompt
    prompt = f"""
أنت بوت خدمة عملاء لمحل "حلويات مصر".
هذه هي المعلومات الرسمية:

{data}

استخدم المعلومات أعلاه فقط للردود.
الرد يكون ودود، مختصر، وواضح باللهجة المصرية.

سؤال العميل: {user_text}
"""

    # DeepSeek API payload
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_KEY}"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "أنت مساعد خدمة عملاء محترف."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        data = response.json()

        if "choices" in data:
            return data["choices"][0]["message"]["content"]

        return "عذرًا، حدث خطأ غير متوقع. حاول مرة أخرى."

    except Exception as e:
        return "في مشكلة تقنية دلوقتي، حاول بعد شوية."
