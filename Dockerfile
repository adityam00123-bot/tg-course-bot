FROM python:3.11-slim

# Install system dependencies (ffmpeg for watermarks/thumbnails and fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy bot application files
COPY . .

# Cloud Port for HuggingFace / Web Platforms
EXPOSE 7860

# Run Bot
CMD ["python", "main.py"]
