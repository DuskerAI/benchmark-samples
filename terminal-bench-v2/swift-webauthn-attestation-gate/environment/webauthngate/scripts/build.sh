#!/bin/bash
set -euo pipefail

mkdir -p /app/webauthngate/bin
swiftc /app/webauthngate/src/Main.swift -o /app/webauthngate/bin/webauthngate
