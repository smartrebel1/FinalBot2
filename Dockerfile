FROM python:3.11-slim

WORKDIR /app

# نسخ وتثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي الملفات
COPY . .

# أمر التشغيل (بدون أقواس عشان يقرأ البورت صح)
CMD uvicorn bot:app --host 0.0.0.0 --port $PORT
