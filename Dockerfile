FROM ubuntu:20.04
WORKDIR /bot

RUN ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    tzdata \
    git \
    apt-utils \
    sqlite3 \
    python3-venv \
    libcairo2-dev \
    libgirepository1.0-dev \
    libpango1.0-dev \
    pkg-config \
    python3-dev \
    gir1.2-pango-1.0 \
    libpython3-dev \
    libjpeg-dev \
    zlib1g-dev \
    python3-pip
RUN python3 -m pip install poetry

COPY ./poetry.lock ./poetry.lock
COPY ./pyproject.toml ./pyproject.toml

RUN python3 -m poetry lock
RUN python3 -m poetry install

COPY . .

ENTRYPOINT ["/bot/run.sh"]
