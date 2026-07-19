# TxLINE Parametric Escrow — Docker deploy
# VPS target: python:3.11-slim + Flask + shared volume

FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source
COPY . .

# Expose Flask port
EXPOSE 5000

# Default: run the engine in mock mode
# Override with CMD in docker-compose per service
CMD ["python", "main.py", "--loop", "--speed", "0.5"]
