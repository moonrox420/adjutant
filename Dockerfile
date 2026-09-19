FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home adjutant
COPY ["Adjutant — Event Registry.json", "/app/Adjutant — Event Registry.json"]
USER adjutant
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz',timeout=3)"
CMD ["python", "-m", "uvicorn", "adjutant.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
