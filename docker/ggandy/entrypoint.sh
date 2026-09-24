#!/bin/bash

set -e

REQUIREMENTS_FILE="/etc/odoo/requirements.txt"

if [ -s "$REQUIREMENTS_FILE" ]; then
    python3 -m pip install --user --break-system-packages --no-cache-dir -r "$REQUIREMENTS_FILE"
fi

exec /entrypoint.sh "$@"
