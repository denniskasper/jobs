FROM python:3.14-bookworm

# Tools the agent uses: git, the GitHub CLI for tickets, jq for the ready-ticket script.
RUN apt-get update && apt-get install -y git curl jq \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*

# The system libraries Chromium needs for the browser tests. Only the libraries: the browser
# itself comes from the mounted Playwright cache, at the version the repository's virtualenv asks
# for when the sandbox starts.
RUN pip install --no-cache-dir playwright \
  && playwright install-deps chromium \
  && pip uninstall -y playwright \
  && rm -rf /var/lib/apt/lists/*

# `sandcastle docker build-image` passes the host's UID and GID, so files the agent writes in the
# bind-mounted worktree belong to the host user.
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN groupadd -o -g $AGENT_GID agent && useradd -o -m -u $AGENT_UID -g $AGENT_GID -d /home/agent -s /bin/bash agent
USER ${AGENT_UID}:${AGENT_GID}

# Claude Code CLI.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/agent/.local/bin:$PATH"

# Never stop to ask: there is no terminal in a sandbox to answer.
ENV CI=true

WORKDIR /home/agent

# Sandcastle bind-mounts the git worktree at /home/agent/workspace and makes it the working
# directory when the container starts.
ENTRYPOINT ["sleep", "infinity"]
