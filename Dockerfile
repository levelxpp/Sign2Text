# Dockerfile - Sign2Text Linux GPU image (secondary to direct VM startup)
#
# Build:
#   docker build -t sign2text .
#
# Run:
#   docker run --gpus all -p 8080:8080 --env-file .env sign2text
#
# This image still expects operator-provided env values, secret files,
# and ASL checkpoint assets. Direct VM startup remains the primary path.

FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
        python3-pip \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        curl \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# Install Ollama (only needed when OLLAMA_MODE is not "remote";
# for remote-only deployments this line can be removed to shrink the image)
RUN curl -fsSL https://ollama.com/install.sh | sh

# Application code
WORKDIR /app
COPY . /app/

# Python dependencies
RUN python -m venv .venv && \
    . .venv/bin/activate && \
    pip install --upgrade pip -q && \
    pip install -r requirements.runtime.txt -q

# Expose ports
# 8000 = ASL, 8001 = Voice, 8002 = Letter, 8003 = Admin, 5500 = Frontend
# 8080 = Proxy (the single public-facing port in --proxy / tunnel mode)
EXPOSE 8000 8001 8002 8003 5500 8080

# Entrypoint
# Uses start_server.sh, which activates the venv, starts Ollama
# (unless OLLAMA_MODE=remote), and launches the coordinated stack in headless mode.
RUN chmod +x start_server.sh setup_runpod.sh
ENTRYPOINT ["bash", "start_server.sh", "--proxy"]
