# Imagen con Python + Playwright + navegadores
FROM mcr.microsoft.com/playwright/python:v1.47.0

# Evitar buffering de logs
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Instalar deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar app
COPY app.py .

# Exponer puerto (Railway setea PORT, pero esto ayuda local)
EXPOSE 8000

# Comando
CMD ["python", "app.py"]
