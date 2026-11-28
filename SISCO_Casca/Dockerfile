# SISCO (casca) em modo demonstração: dados inventados, sem envio de e-mail, base reiniciada de tempos em tempos.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=America/Sao_Paulo SISCO_DEMO=1 PORT=8080
WORKDIR /app
COPY . .
RUN useradd --create-home sisco && mkdir -p dados entrada && chown -R sisco:sisco dados entrada modelos
USER sisco
EXPOSE 8080
CMD ["python", "app.py"]
