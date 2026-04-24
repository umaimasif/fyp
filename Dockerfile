FROM python:3.11-slim

WORKDIR /app

# System dependencies for OpenCV (headless) and PDF generation
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libgomp1 \
    libgl1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Download YOLO model from GitHub at build time
RUN wget -q "https://github.com/umaimasif/fyp/raw/main/Numberplate.pt" -O Numberplate.pt

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
