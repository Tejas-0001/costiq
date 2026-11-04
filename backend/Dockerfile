# MM CostIQ API: FastAPI + DuckDB.
# One tenant pack per image (backend/tenants/<tenant>): docker build --build-arg COSTIQ_TENANT=utility .
# The tenant's sample database is built into the image at /app/sample/<tenant>. On first start the entrypoint copies it
# to the /data volume, so demo changes (approvals, evidence packs, the ledger) survive restarts until the volume is removed.
FROM python:3.12-slim
ARG COSTIQ_TENANT=pharma
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 COSTIQ_TENANT=${COSTIQ_TENANT}
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt && useradd --uid 10001 --create-home costiq
COPY costiq costiq
COPY tenants tenants
COPY schema schema
COPY registry registry
COPY data data
COPY tools tools
COPY tests tests
RUN python data/build_sample.py --tenant "$COSTIQ_TENANT" \
    && rm -rf "sample/$COSTIQ_TENANT/csv" sample/*/*.duckdb.wal \
    && mkdir -p /data && chown costiq /data
COPY --chmod=0555 docker-entrypoint.sh /usr/local/bin/costiq-entrypoint
USER 10001
ENV COSTIQ_DB_URL=duckdb:////data/costiq.duckdb COSTIQ_AUTH_MODE=demo
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
ENTRYPOINT ["costiq-entrypoint"]
CMD ["serve"]
