FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app \
    && mkdir -p /app/config/cache /app/attachments \
    && chown -R app:app /app/config /app/attachments

# backend (8000) and gateway (8001) share this image; the command
# picks the service.
ENV MODULE=app.main:app
EXPOSE 8000 8001

USER app

CMD ["sh", "-c", "uvicorn ${MODULE} --host 0.0.0.0 --port ${PORT:-8000}"]
