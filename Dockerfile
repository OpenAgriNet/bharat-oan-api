# Use Python as the base image
FROM python:3.15-slim

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    gcc \
    g++ \
    libyaml-dev \
    python3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.lock requirements-build.lock ./

# Add pip index
RUN pip config set global.index-url https://pypi.org/simple

# Hash-lock build tools before disabling isolation for the two source builds.
RUN python3 -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements-build.lock
# These two packages do not publish CPython 3.15 wheels yet.
RUN python3 -m pip install --no-cache-dir --no-build-isolation \
    --only-binary=:all: --no-binary=marisa-trie,pyyaml \
    --require-hashes -r requirements.lock
RUN python3 -m pip check && python3 -c \
    "import httptools, marisa_trie, numpy, pandas, pydantic, rapidfuzz, scipy, torch, zope.interface; from yaml import CLoader; from langcodes import Language; from sentence_transformers import SentenceTransformer; assert torch.version.cuda is None, 'Expected CPU-only Torch'; assert Language.get('hi').display_name('en') == 'Hindi'"

# Bake E5 embedding model into the image (avoids HuggingFace download on first search_schemes call)
ARG HF_TOKEN=
ENV HF_TOKEN=${HF_TOKEN}
ENV HF_HOME=/opt/hf-cache
ENV CUDA_VISIBLE_DEVICES=
RUN mkdir -p /opt/hf-cache && python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-large')"

# Copy application code
COPY . .

# Create logs directory for supervisord
RUN mkdir -p /app/logs

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create non-root user for security (optional)
# RUN useradd --create-home --shell /bin/bash app
# RUN chown -R app:app /app
# USER app

# Expose FastAPI port
EXPOSE 8000

# Start supervisor to manage both FastAPI and Celery
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]