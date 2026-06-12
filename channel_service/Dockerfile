FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY channel_service/ channel_service/

RUN useradd --create-home appuser
USER appuser

EXPOSE 8001
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/healthz')"

CMD ["uvicorn", "channel_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
