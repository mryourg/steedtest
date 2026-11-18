FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir requests==2.32.3

COPY speedtest.py .

ENTRYPOINT ["python", "speedtest.py"]
