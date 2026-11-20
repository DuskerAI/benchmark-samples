#!/bin/bash
set -euo pipefail

install -m 0644 /solution/Main.swift /app/webauthngate/src/Main.swift
/app/webauthngate/scripts/build.sh
