# Sube la imagen a v1.55.0-noble (match con la lib)
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Instala solo tus deps (sin playwright)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
CMD ["python", "app.py"]
