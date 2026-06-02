# Solace Web — container image for Render / Railway.
# Runs ONLY the web layer (webapp.py). The desktop solace.py is imported as a
# library for its emotion model / playlist generator; its Tkinter GUI never runs
# (webapp.py supplies a stub tkinter when the real one is absent on the server).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Single worker keeps the read-only library in one place; 512MB-friendly.
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8000} -w 1 -t 120 webapp:app"]
