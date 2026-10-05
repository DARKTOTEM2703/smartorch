FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl git \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python primero (cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código del servidor
COPY . .

# Puerto
EXPOSE 8080

# Arranque
ENV SMARTORCH_HOST=0.0.0.0
ENV SMARTORCH_PORT=8080
ENV OLLAMA_URL=http://ollama:11434

CMD ["python", "run.py", "--no-watch"]
