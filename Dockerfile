FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV PHOTO_DATA_DIR=/data
EXPOSE 8000
CMD ["sh", "-c", "photo-manager serve --host 0.0.0.0 --port ${PORT:-8000}"]
