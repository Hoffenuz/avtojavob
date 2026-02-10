# 1. Python 3.11 versiyasini olamiz (sizdagi logda 3.11 ko'rindi)
FROM python:3.11-slim

# 2. ENG MUHIMI: Tizimga kerakli C++ va Grafik kutubxonalarni o'rnatamiz
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libstdc++6 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 3. Ishchi papkani belgilaymiz
WORKDIR /app

# 4. requirements.txt ni ko'chirib, o'rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Qolgan hamma fayllarni ko'chiramiz
COPY . .

# 6. Botni ishga tushiramiz (fayl nomingiz main.py bo'lsa)
CMD ["python", "main.py"]
