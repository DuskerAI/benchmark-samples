# Uses the canonical Terminal-Bench Debian distro base; the canonical image list
# does not currently include a .NET/F# SDK runtime, so dotnet-sdk-8.0 is installed
# during the image build from Microsoft's verified Debian package feed.
FROM public.ecr.aws/docker/library/debian:bookworm-slim@sha256:4724b8cc51e33e398f0e2e15e18d5ec2851ff0c2280647e1310bc1642182655d

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    DOTNET_CLI_HOME=/app/.dotnet \
    NUGET_PACKAGES=/app/.nuget/packages

ARG MS_PROD_DEB_SHA256=8434dcb8c346dc95fbd63dbece056c343704590b58b6a5c323d39acf52bf0b48
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        curl \
        python3 \
        python-is-python3 \
        python3-pip \
        tmux \
    && curl -fsSL "https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb" -o /tmp/packages-microsoft-prod.deb \
    && echo "${MS_PROD_DEB_SHA256}  /tmp/packages-microsoft-prod.deb" | sha256sum -c - \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        dotnet-sdk-8.0 \
    && python3 -m pip install --break-system-packages --no-cache-dir \
        pytest==8.4.1 \
        pytest-json-ctrf==0.3.5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/nugate
COPY task_file/ /app/nugate/
RUN groupadd --system agent \
    && useradd --system --gid agent --home-dir /home/agent --create-home agent \
    && mkdir -p /app/.dotnet /app/.nuget/packages \
    && chmod +x /app/nugate/bin/build.sh \
    && dotnet restore /app/nugate/src/Nugate.fsproj --nologo \
    && chown -R agent:agent /app /home/agent \
    && chown root:agent /app/nugate/bin \
    && chmod 1775 /app/nugate/bin \
    && chown root:root /app/nugate/bin/build.sh \
    && chmod 0555 /app/nugate/bin/build.sh
