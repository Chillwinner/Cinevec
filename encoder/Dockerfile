FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Optional PyTorch wheel index (e.g. https://download.pytorch.org/whl/cpu for smaller CPU-only images)
ARG TORCH_INDEX_URL=""

COPY requirements.txt /app/requirements.txt
RUN if [ -n "$TORCH_INDEX_URL" ]; then \
        pip install --no-cache-dir "torch>=2.4.0" --index-url "$TORCH_INDEX_URL"; \
    else \
        pip install --no-cache-dir "torch>=2.4.0"; \
    fi \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY server.py /app/server.py

ENV HF_HOME=/cache/huggingface

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
