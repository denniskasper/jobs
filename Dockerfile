FROM python:3.14-slim AS runtime

# git is a runtime dependency: every mutation of the store ends in a commit, and the store
# itself is a clone kept inside the volume. curl is what the installer below downloads with.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/sh -u 1001 app

# The mount point is created here, owned by the unprivileged user, because a fresh named volume
# takes its ownership from the directory it is mounted over.
RUN mkdir /data && chown app:app /data

# Registering a Source asks a model through the `claude` command (decision 39, ticket 15). It is a
# single glibc-linked binary, so no node runtime comes with it, and it installs under the
# unprivileged user's home rather than system-wide. The version is pinned: a rebuild gets the one
# the flags were checked against, not whatever is current. No credential is baked in — the command
# reads CLAUDE_CODE_OAUTH_TOKEN from the deployment's environment at run time. This sits above the
# code because it is the slowest layer to build and the one that changes least. The home is opened
# to every user because compose.yaml runs the image as whoever starts it, not as `app`, and a home
# only its owner may enter leaves that user unable to run the command at all.
ENV HOME=/home/app \
    PATH=/home/app/.local/bin:$PATH
USER app
RUN curl -fsSL https://claude.ai/install.sh | bash -s 2.1.278 \
    && chmod 755 /home/app

# Root again, because `playwright install --with-deps` installs system libraries
# through apt. pip then warns that running as root can conflict with the system
# package manager; in this image it cannot, because pip installs into /usr/local
# while Debian's own Python lives in /usr/lib and the two never meet. The warning is
# suppressed rather than worked around: a virtual environment would buy nothing here
# and would complicate the install of the package itself below. The service still
# runs as `app` from the USER line further down, only ever reading what root wrote.
USER root

# The browser that renders the listings a script fills in, with the system libraries it needs.
# Installed before the source is copied so that the layer, the largest in the image, survives
# every change to the code; the version is pinned because a Playwright only drives the Chromium
# build it was released with. Kept outside any home directory, where the unprivileged user finds it.
ARG PLAYWRIGHT_VERSION=1.63.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install --no-cache-dir --root-user-action=ignore "playwright==${PLAYWRIGHT_VERSION}" \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --root-user-action=ignore .

USER app

# FORWARDED_ALLOW_IPS is uvicorn's own variable. Behind Traefik the service is reached over plain
# HTTP and only the X-Forwarded-Proto header says the visitor came by HTTPS; trusted from
# localhost alone, the default, every generated URL comes out as http:// and the browser refuses
# the stylesheet as mixed content. Any sender is trusted because the port is not published: the
# router is the only thing that can reach it.
ENV DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=* \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8000

# The start period covers the first start, which clones the store before it serves anything.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["python", "-m", "jobs"]
