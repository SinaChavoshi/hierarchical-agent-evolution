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

# Everything the image needs to reproduce its own run. Generation 10 mounted
# four .py files over this image via a ConfigMap because they were not baked
# in, which meant the published image did not correspond to the published
# results. Nothing is patched in at runtime any more.
COPY hae/ /app/hae/
COPY templates/ /app/templates/
COPY configs/ /app/configs/
COPY scripts/ /app/scripts/
COPY tests/ /app/tests/
COPY pyproject.toml /app/pyproject.toml

ENV PYTHONPATH=/app

# Fail the build rather than ship an image that cannot verify anything. Each
# import below is a hard dependency of a specific execution gate, and the
# module imports catch a broken refactor before a tournament does.
RUN python -c "import ast, importlib.util, pytest, opentelemetry; \
    assert importlib.util.find_spec('pip'), 'build gate needs pip'; \
    import hae.evaluation.harness, hae.evaluation.judge, hae.evaluation.artifacts; \
    import hae.evaluation.benchmark, hae.orchestration.worker, hae.cli; \
    print('harness imports OK')"

# The reachability guard runs at build time too: an image will not ship with a
# capability module that no entry point can reach. Three such modules named
# two V1 generations without ever executing.
RUN python -m unittest tests.test_architecture -v

ENTRYPOINT ["python", "-m", "hae.cli"]
CMD ["--help"]
