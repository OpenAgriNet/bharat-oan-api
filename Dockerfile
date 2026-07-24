# Use Python as the base image
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Debian's mirrors now reject plain HTTP (403) — force HTTPS before apt-get update
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

# Install system dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    gcc \
    python3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Add pip index
RUN pip config set global.index-url https://pypi.org/simple

# Install Python dependencies
RUN python3 -m pip install --upgrade pip && python3 -m pip install --upgrade setuptools && python3 -m pip install --upgrade wheel
# CPU-only torch first — avoids huge NVIDIA CUDA wheels on aarch64 Docker builds
RUN python3 -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN grep -v '^sentence-transformers' requirements.txt > /tmp/requirements-base.txt \
    && python3 -m pip install --no-cache-dir -r /tmp/requirements-base.txt \
    && python3 -m pip install --no-cache-dir sentence-transformers

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