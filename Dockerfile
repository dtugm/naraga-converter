# syntax=docker/dockerfile:1.7
# Production image. Same conda-forge base as dev so what you test is what ships.
FROM mambaorg/micromamba:1.5.10-noble

# Pin the mago-3d-tiler release and its sha256 so the download is reproducible and
# tamper-evident (same JAR used in Task C's readiness check via MAGO_TILER_JAR).
ARG MAGO_TILER_VERSION=1.15.4
ARG MAGO_TILER_SHA256=e83b961d122762cf03e57795723fb6d79c452a01d3beb1e382d34c7d39733a40

USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl tini \
 && rm -rf /var/lib/apt/lists/*

# mago-3d-tiler JAR (Java 3D Tiles conversion tool). Downloaded and checksummed as
# root, then made world-readable so $MAMBA_USER can read it at runtime.
RUN mkdir -p /opt/mago-3d-tiler \
 && curl -fL --retry 5 --retry-delay 10 --connect-timeout 30 --max-time 600 \
      -o /opt/mago-3d-tiler/mago-3d-tiler.jar \
      "https://github.com/Gaia3D/mago-3d-tiler/releases/download/v${MAGO_TILER_VERSION}/mago-3d-tiler-${MAGO_TILER_VERSION}.jar" \
 && echo "${MAGO_TILER_SHA256}  /opt/mago-3d-tiler/mago-3d-tiler.jar" | sha256sum -c - \
 && chmod a+r /opt/mago-3d-tiler/mago-3d-tiler.jar

USER $MAMBA_USER

WORKDIR /app

RUN micromamba install -y -n base -c conda-forge \
      python=3.12 gdal=3.9 pdal=2.8 python-pdal laspy \
      tippecanoe openjdk=21 rasterio scipy lazrs-python \
 && micromamba clean --all --yes
RUN micromamba run -n base python -m pip install --no-cache-dir pmtiles==3.7.0

COPY --chown=$MAMBA_USER:$MAMBA_USER pyproject.toml ./
COPY --chown=$MAMBA_USER:$MAMBA_USER src ./src
# '.[geo]' on purpose: the real run_job imports torch/rasterio/etc., and a prod image
# missing them fails at runtime while dev (which installs the extra) passes. The image
# is large; that is the honest cost of shipping the pipeline's dependencies.
RUN micromamba run -n base python -m pip install --no-cache-dir '.[geo]'

# Durable job state (SQLite) lives here; MOUNT A VOLUME over it in deployment —
# a rescheduled pod loses un-mounted container disk, and with it the callback outbox.
RUN mkdir -p /app/data /tmp/naraga-converter

ENV PATH=/opt/conda/bin:$PATH PYTHONUNBUFFERED=1
EXPOSE 8085

# tini as PID 1 so SIGTERM reaches uvicorn and in-flight work drains.
ENTRYPOINT ["/usr/bin/tini", "--"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8085/health || exit 1
CMD ["micromamba", "run", "-n", "base", "uvicorn", "converter.main:app", \
     "--host", "0.0.0.0", "--port", "8085", "--timeout-graceful-shutdown", "30"]
