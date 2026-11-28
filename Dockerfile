FROM python:3.12-slim

# Install CA certificates, curl (for Docker HEALTHCHECK), and ffmpeg (compact MP3/M4A transcoding)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install optional cloud streaming & decryption dependencies (cryptography + yt-dlp)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Run as non-root UID 1000 for container security
RUN useradd -m -u 1000 user

WORKDIR /home/user/app

# Copy application source owned by non-root user
COPY --chown=user:user app.py README.md requirements.txt ./
COPY --chown=user:user backend ./backend
COPY --chown=user:user frontend ./frontend

# Create writable SQLite data directory owned by user:user
RUN mkdir -p /home/user/app/data && chown -R user:user /home/user/app/data

USER user

ENV HOME=/home/user \
    PATH="/home/user/.local/bin:${PATH}" \
    SONIFY_HOST=0.0.0.0 \
    PORT=10000 \
    PYTHONUNBUFFERED=1

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/api/health || exit 1

CMD ["python3", "app.py"]
