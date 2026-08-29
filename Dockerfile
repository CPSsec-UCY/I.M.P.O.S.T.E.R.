# IMPOSTER — Industrial Modelling & Protocol Simulation Testbed
# Multi-stage not required: the app is a single-process Flask server with
# real protocol listeners (Modbus TCP / IEC 104 / GOOSE / MQTT).

FROM python:3.11-slim

# Avoid interactive prompts and keep Python output unbuffered so logs stream.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first to leverage layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the application source.
COPY . .

# Web HMI + REST API, Modbus TCP (5020-5023), IEC 60870-5-104 (2404-2407),
# GOOSE gateway (5880-5883), MQTT (1883).
EXPOSE 5000 5020 5021 5022 5023 2404 2405 2406 2407 5880 5881 5882 5883 1883

# The simulator manager and protocol listeners are started inside app.py.
CMD ["python3", "app.py"]
