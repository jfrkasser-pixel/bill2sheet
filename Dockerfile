FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /data
ENTRYPOINT ["bill2sheet"]
CMD ["--help"]
