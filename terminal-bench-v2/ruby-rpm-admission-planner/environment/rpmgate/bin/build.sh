#!/bin/sh
set -eu
ruby -c /app/rpmgate/bin/rpm-admit >/dev/null
chmod +x /app/rpmgate/bin/rpm-admit
