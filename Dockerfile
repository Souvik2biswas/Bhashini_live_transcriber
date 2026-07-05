# Use an official Python runtime as a parent image (compatible with Python 3.12)
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

# Set working directory
WORKDIR /code

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user with UID 1000 (required by Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Copy requirements and install
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy project files
COPY --chown=user:user . .

# Run database migrations (optional, but good practice) and collect static files
RUN python manage.py migrate --noinput && \
    python manage.py collectstatic --noinput

# Expose port 7860 (Hugging Face Spaces routes external traffic to this port)
EXPOSE 7860

# Run ASGI server via Daphne binding to port 7860
CMD ["daphne", "-b", "0.0.0.0", "-p", "7860", "bhashini_web.asgi:application"]
