FROM python:3.12-slim

# ตั้งโซนเวลาประเทศไทย + ประหยัด RAM สูงสุดเพื่อให้รันในโควตาฟรี $1/เดือน ของ Railway ได้สบายๆ
ENV TZ=Asia/Bangkok
ENV PYTHONUNBUFFERED=1
ENV PYTHONOPTIMIZE=1
ENV MALLOC_TRIM_THRESHOLD_=65536

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
RUN grep -v '^imageio-ffmpeg' requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt \
    && rm -f /tmp/req.txt

COPY . .

# สร้างโฟลเดอร์สำหรับเก็บฐานข้อมูลถาวร (กรณีผูก Persistent Volume บน Cloud)
RUN mkdir -p /data /app/sounds

CMD ["python", "-O", "-u", "bot.py"]
