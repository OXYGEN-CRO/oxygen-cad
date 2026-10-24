FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY catalog-requirements.lock ./
RUN pip install --no-cache-dir -r catalog-requirements.lock
COPY src/o3d/ src/o3d/
RUN useradd --create-home --uid 10001 catalog && mkdir /data && chown catalog:catalog /data
USER catalog
ENV O3D_CATALOG_DATA=/data/objects FASTEMBED_CACHE_PATH=/data/models
EXPOSE 8844
CMD ["sh", "-c", "python -m o3d.catalog init && exec python -m o3d.catalog serve --host 0.0.0.0"]
