FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    mono-complete libopenal1 liblua5.4-0 lsb-release libgdiplus \
    libgl1-mesa-dri libgl1 libx11-6 xvfb xauth ffmpeg fonts-dejavu-core \
    procps ncurses-bin ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 TERM=xterm ALSOFT_DRIVERS=null
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jev.py env_config.py ./
COPY src ./src
COPY bridge ./bridge
COPY container-entry.sh ./
ENTRYPOINT ["sh", "/app/container-entry.sh"]
