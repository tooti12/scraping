#!/bin/bash
# entrypoint.sh — validates environment and cleans stale runtime state
# before handing off to the real command (python main.py).
set -euo pipefail

# ── Require all credentials to be present ────────────────────────────────────
# Failing fast here is much better than a cryptic Python traceback several
# minutes later when Chrome has already launched and a login attempt fails.
REQUIRED_VARS=(
    VFS_EMAIL
    VFS_PASSWORD
    GMAIL_APP_PASSWORD
    LEGACY_EMAIL_PASSWORD
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM_NUMBER
    TWILIO_TO_NUMBER
    VFS_PROXY_URL
)

missing=0
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        echo "[entrypoint] ERROR: required env var '$var' is not set." >&2
        missing=1
    fi
done
if [ "$missing" -eq 1 ]; then
    echo "[entrypoint] Copy .env.example -> .env and fill in all values, then restart." >&2
    exit 1
fi

# ── Clean stale lock files from a previous (crashed) run ─────────────────────
# SeleniumBase writes .lock files in downloaded_files/ while patching
# the ChromeDriver binary and building the proxy extension. If the container
# was killed mid-operation those locks are never cleaned up, and the next
# run finds them and either waits forever or skips the patch step entirely
# (leaving an unpatched binary that Cloudflare can fingerprint).
if [ -d /app/downloaded_files ]; then
    find /app/downloaded_files -maxdepth 2 -name "*.lock" -delete 2>/dev/null || true
    # Wipe the proxy extension directory — it's rebuilt fresh on each launch
    # and a half-written extension from a crash causes Chrome to fail to load
    # it ("Manifest file is missing or unreadable").
    rm -rf /app/downloaded_files/proxy_ext_dir 2>/dev/null || true
fi

echo "[entrypoint] Environment OK. Starting: $*"
exec "$@"
