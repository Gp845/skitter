FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates libgl1 libglib2.0-0 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY storage/package.json storage/package-lock.json storage/
RUN cd storage && npm ci --omit=dev
COPY . .

# Railway: mount a volume at /data so accounts (sealed wallets, keys, maps) and the owner's episodes survive redeploys
ENV HOST=0.0.0.0 SKITTER_ACCOUNTS=/data/accounts SKITTER_HOME=/data/episodes PYTHONUNBUFFERED=1
CMD ["python", "hub.py", "--world", "house"]
