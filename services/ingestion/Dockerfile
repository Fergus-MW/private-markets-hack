FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NLTK_DATA=/usr/local/share/nltk_data
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 poppler-utils tesseract-ocr libgl1 libglib2.0-0 libreoffice-writer libreoffice-calc libreoffice-impress pandoc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m nltk.downloader -d /usr/local/share/nltk_data punkt_tab averaged_perceptron_tagger_eng
COPY app ./app
RUN useradd --uid 10001 --create-home worker
USER worker
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
