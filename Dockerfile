# Imagen alineada con la versión actual estable (ajústala si cambia):
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# No instales playwright vía pip: YA viene en la imagen
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
CMD ["python", "app.py"]
