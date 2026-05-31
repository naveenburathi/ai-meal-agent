# Use a stable official Python slim runtime as parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Set work directory
WORKDIR /app

# Install system dependencies (none are strictly needed for pure python pg8000, but nice to have standard tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY app.py /app/

# Expose port (Render requires bound port for health checks if deployed as web service, otherwise running as background worker is fine)
EXPOSE 10000

# Command to run the application
CMD ["python", "app.py"]
