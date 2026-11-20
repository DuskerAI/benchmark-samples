#!/bin/sh
set -eu
cd /app/nugate
mkdir -p /app/nugate/bin /app/nugate/build
rm -f /app/nugate/bin/nugate /app/nugate/bin/Nugate
dotnet publish /app/nugate/src/Nugate.fsproj -c Release -o /app/nugate/bin -p:UseAppHost=true --no-restore --nologo
if [ -x /app/nugate/bin/Nugate ]; then
  mv /app/nugate/bin/Nugate /app/nugate/bin/nugate
fi
chmod +x /app/nugate/bin/nugate
