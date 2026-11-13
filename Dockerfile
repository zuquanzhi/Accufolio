FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 app
COPY apps ./apps
COPY packages ./packages
COPY sql ./sql
COPY assets ./assets
RUN mkdir -p /data && chown app:app /data
USER app
ENV ACCUFOLIO_DB=/data/accufolio.db
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
