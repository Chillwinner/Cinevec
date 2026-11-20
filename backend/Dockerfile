FROM python:3.11-slim

WORKDIR /app

ARG INSTALL_FACE=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir "pymilvus[milvus_lite]>=2.4.9" \
    && if [ "$INSTALL_FACE" = "true" ]; then \
        pip install --no-cache-dir onnxruntime numpy; \
    fi

COPY app /app/app

RUN mkdir -p /app/data/work

EXPOSE 8008

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
