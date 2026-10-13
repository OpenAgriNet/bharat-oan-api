# IMD agromet loader: docker run --rm -e IMD_DB_URL=... imd-agromet setup|seed-keys|fetch|status
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir --only-binary :all: psycopg2-binary==2.9.10 httpx==0.28.1
COPY schema.sql imd_agromet.py ./
COPY masters ./masters
ENV PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
USER app
ENTRYPOINT ["python", "imd_agromet.py"]
CMD ["status"]
