FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# build-essential is needed at runtime, not just at build time: the execution
# harness runs `pip install -e .` on candidate packages, some of which declare
# dependencies with C extensions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source, templates and configs.
COPY src/ /app/src/
COPY templates/ /app/templates/
COPY configs/ /app/configs/

# Harvest, breeding and backfill scripts. Generation 10 had to mount four .py
# files over this image via the `gen10-code` ConfigMap because they were not
# baked in, which meant the image did not reproduce its own run. Copying them
# retires that pattern.
COPY scripts/ /app/scripts/
COPY pyproject.toml /app/pyproject.toml

ENV PYTHONPATH=/app

# Fail the build rather than ship an image that cannot verify anything. Each of
# these is a hard dependency of a specific execution gate.
RUN python -c "import ast, importlib.util, pytest, opentelemetry; \
    assert importlib.util.find_spec('pip'), 'build gate needs pip'; \
    import src.execution_harness, src.evaluator, src.artifacts; \
    print('harness imports OK')"

ENTRYPOINT ["python", "-m", "src.main"]
CMD ["--mode", "tournament"]
