# ────────────────────────────────────────────────────────────────────────────
# VFS Slot Checker / Booking Bot — Docker image
#
# Runs python main.py which starts the Flask dashboard on port 5050.
# Chrome is launched by SeleniumBase workers on demand (when a visitor
# clicks "Start Bot" on /availability) using a per-session Xvfb virtual
# display — no physical screen or real X server required.
#
# Build:  docker compose build
# Start:  docker compose up -d
# Logs:   docker compose logs -f
# ────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Download tools
    wget curl gnupg ca-certificates unzip \
    # Chrome runtime dependencies
    fonts-liberation libnss3 libxss1 libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libgbm-dev \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxi6 \
    libx11-xcb1 libxcb1 libxcb-dri3-0 \
    # Xvfb — SeleniumBase's xvfb=True spawns a virtual display per session
    # so simultaneous Cloudflare-solving clicks never land on the wrong window
    xvfb \
    # xdotool — screen_keeper.py uses this for mouse jiggle on Linux
    xdotool \
    # tesseract-ocr — optional; only needed if VFS sends OTP as a CAPTCHA image
    tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# ── Google Chrome stable ──────────────────────────────────────────────────────
RUN wget -q -O /tmp/signing.key https://dl.google.com/linux/linux_signing_key.pub \
 && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /tmp/signing.key \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/* /tmp/signing.key

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Installed before COPY . . so Docker's layer cache keeps this step warm
# across code-only changes — only re-runs when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── ChromeDriver: download matching version ───────────────────────────────────
# SeleniumBase's UC patcher looks for the binary at:
#   <CWD>/downloaded_files/undetected_chromedriver
# We fetch the exact version that matches the installed Chrome so the driver
# and browser agree on the DevTools protocol version.
RUN set -e \
 && CHROME_VERSION=$(google-chrome --version | grep -oP '[\d.]+') \
 && CHROME_MAJOR=$(echo "$CHROME_VERSION" | cut -d. -f1) \
 && echo "Installed Chrome: $CHROME_VERSION  (major: $CHROME_MAJOR)" \
 && CDVER=$(curl -fsSL \
      "https://googlechromelabs.github.io/chrome-for-testing/LATEST_RELEASE_${CHROME_MAJOR}") \
 && echo "Fetching ChromeDriver $CDVER ..." \
 && curl -fsSL \
      "https://storage.googleapis.com/chrome-for-testing-public/${CDVER}/linux64/chromedriver-linux64.zip" \
      -o /tmp/chromedriver.zip \
 && mkdir -p downloaded_files \
 && unzip -j /tmp/chromedriver.zip "chromedriver-linux64/chromedriver" -d downloaded_files/ \
 && mv downloaded_files/chromedriver downloaded_files/undetected_chromedriver \
 && rm /tmp/chromedriver.zip

# ── ChromeDriver: patch cdc_ → abc_ ──────────────────────────────────────────
# Replace every "cdc_" token in the binary with "abc_" so Chrome's
# navigator.webdriver fingerprint check passes (Cloudflare reads this token).
# Making the file read-only prevents SeleniumBase's patcher from overwriting
# it on startup — it calls is_binary_patched(), sees True, and skips cleanly.
RUN python3 <<'PYEOF'
import sys

path = "downloaded_files/undetected_chromedriver"
data = open(path, "rb").read()
n = data.count(b"cdc_")
if n == 0:
    print("WARNING: no 'cdc_' tokens found — binary may already be patched or is wrong version")
    sys.exit(0)
patched = data.replace(b"cdc_", b"abc_")
open(path, "wb").write(patched)
print(f"Patched {n} occurrence(s) of 'cdc_' -> 'abc_' in {path}")
PYEOF
RUN chmod a-w downloaded_files/undetected_chromedriver

# ── Runtime directories ───────────────────────────────────────────────────────
# docker-compose mounts these as bind volumes. Create them here so the image
# also works standalone without a compose file (docker run ...).
RUN mkdir -p logs browser_sessions

# ── Expose the Flask dashboard ────────────────────────────────────────────────
EXPOSE 5050

# ── Entrypoint validates env vars and cleans stale locks before starting ──────
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
