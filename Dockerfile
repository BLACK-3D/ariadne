FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ARIADNE_HOST=0.0.0.0 ARIADNE_DATA_DIR=/app/data
WORKDIR /app
COPY requirements-server.txt requirements-documents.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-server.txt -r requirements-documents.txt && useradd --uid 10001 --create-home ariadne && mkdir /app/data && chown ariadne:ariadne /app/data
COPY ariadne ./ariadne
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["python", "-m", "ariadne", "serve"]
