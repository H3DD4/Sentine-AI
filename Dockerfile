FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

# Serving is offline-only. Warm the load-bearing retrieval models into the image
# so a clean clone can start without downloading during the first request.
RUN ALLOW_MODEL_DOWNLOADS=true python -m scripts.warm_models --only dense \
    && ALLOW_MODEL_DOWNLOADS=true python -m scripts.warm_models --only sparse

EXPOSE 8002

CMD ["python", "run.py"]
