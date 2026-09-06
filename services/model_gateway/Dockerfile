FROM python:3.12-slim
WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway ./gateway
USER 65532:65532
CMD ["sh", "-c", "exec uvicorn gateway.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
