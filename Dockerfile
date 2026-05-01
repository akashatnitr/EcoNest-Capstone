FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libmariadb-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy dependency definitions
COPY pyproject.toml poetry.lock ./

# Install dependencies (without the package itself, since we mount the code)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-root --extras dev

# Default command (overridden by docker-compose)
CMD ["poetry", "run", "poe", "dev"]
