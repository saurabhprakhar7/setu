FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so they cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Persisted resumes + LinkedIn token live here (mounted as a volume in compose).
ENV RESUMES_DIR=/app/data/resumes \
    LINKEDIN_TOKEN_STORE=/app/data/linkedin_token.json

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.getenv('PORT','8000'))"

# Honour Render's $PORT; default to 8000 locally. exec → uvicorn is PID 1 (clean signals).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
