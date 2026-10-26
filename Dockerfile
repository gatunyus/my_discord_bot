FROM python:3.12-slim

# ตั้งโซนเวลาประเทศไทยเพื่อให้ระบบ ☀️/🌙 และแจ้งเตือนตรงเวลาไทย
ENV TZ=Asia/Bangkok
ENV PYTHONUNBUFFERED=1

# ติดตั้งระบบเสียง (ffmpeg + libopus0) และฟอนต์ภาษาไทยสำหรับสร้างรูปคำคม (/quote)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    fonts-thai-tlwg \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# สร้างโฟลเดอร์สำหรับเก็บฐานข้อมูลถาวร (กรณีผูก Persistent Volume บน Cloud)
RUN mkdir -p /data /app/sounds

CMD ["python", "-u", "bot.py"]
