FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Create non-root user
RUN useradd -m -s /bin/bash agent && \
    chown -R agent:agent /app
USER agent

# Default: run user agent in dry-run mode
CMD ["python", "user_agent.py", "--once", "--dry-run"]
