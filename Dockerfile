FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY web_data.py .

ENV PORT=8000
EXPOSE 8000

CMD gunicorn web_data:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 300
