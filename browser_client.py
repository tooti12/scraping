# browser_client.py
import glob
import json
import os
import re
import shutil
import stat

from selenium.common.exceptions import TimeoutException
from seleniumbase import SB

from config import COUNTRY_CONFIG, PROXY

# ---------------------------------------------------------------------------
# Chromedriver management
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# On Linux: the UC patcher cannot detect Chrome 147's version and falls back
# to downloading chromedriver 114 — a fatal mismatch.
# Fix: copy chromedriver 147, patch out `cdc_` strings so is_binary_patched()
# returns True, then make the file read-only. The patcher gets PermissionError
# on its unlink attempt, sees it is already patched, and uses it as-is.
# On Windows: SeleniumBase / UC handles chromedriver automatically — no fix needed.
# ---------------------------------------------------------------------------

import platform as _platform

_IS_LINUX = _platform.system() == "Linux"
_IS_WINDOWS = _platform.system() == "Windows"

_CHROMEDRIVER_SOURCE_LINUX = (
    "/home/dev/.cache/selenium/chromedriver/linux64/147.0.7727.117/chromedriver"
)


def _get_uc_driver_path() -> str:
    from seleniumbase.core.download_helper import get_downloads_folder
    name = "undetected_chromedriver.exe" if _IS_WINDOWS else "undetected_chromedriver"
    return os.path.join(get_downloads_folder(), name)


def _ensure_chromedriver_147() -> None:
    """Linux-only fix: install + lock chromedriver 147 so patcher uses it."""
    if not _IS_LINUX:
        return  # Windows / macOS: UC patcher manages driver automatically

    target = _get_uc_driver_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)

    # Make writable before writing (might be read-only from last run)
    if os.path.exists(target):
        os.chmod(target, 0o755)

    if not os.path.exists(_CHROMEDRIVER_SOURCE_LINUX):
        raise RuntimeError(
            f"ChromeDriver 147 source not found at {_CHROMEDRIVER_SOURCE_LINUX}.\n"
            "Download it with:\n"
            "  curl -L https://storage.googleapis.com/chrome-for-testing-public/"
            "147.0.7727.117/linux64/chromedriver-linux64.zip -o /tmp/cd.zip\n"
            "  unzip /tmp/cd.zip -d /tmp/cd\n"
            f"  cp /tmp/cd/chromedriver-linux64/chromedriver {_CHROMEDRIVER_SOURCE_LINUX}\n"
            f"  chmod +x {_CHROMEDRIVER_SOURCE_LINUX}"
        )

    shutil.copy2(_CHROMEDRIVER_SOURCE_LINUX, target)
    os.chmod(target, 0o755)

    # Patch: replace every `cdc_` occurrence so is_binary_patched() → True
    with open(target, "rb") as f:
        content = f.read()
    if b"cdc_" in content:
        content = content.replace(b"cdc_", b"abc_")
        with open(target, "wb") as f:
            f.write(content)

    # Read-only → patcher gets PermissionError on unlink → skips re-download
    os.chmod(
        target,
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )
    print(f"[BrowserClient] ChromeDriver 147 ready at {target}", flush=True)


# ---------------------------------------------------------------------------
# Lock-file cleanup
# ---------------------------------------------------------------------------

def _clear_chrome_locks(user_data_dir: str) -> None:
    """Remove Chrome singleton lock files left by a killed process."""
    for pattern in [
        f"{user_data_dir}/SingletonLock",
        f"{user_data_dir}/SingletonCookie",
        f"{user_data_dir}/SingletonSocket",
        f"{user_data_dir}/.com.google.Chrome.*",
    ]:
        for path in glob.glob(pattern):
            try:
                os.remove(path)
                print(f"[BrowserClient] Removed lock file: {path}", flush=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# BrowserClient
# ---------------------------------------------------------------------------

class BrowserClient:
    def __init__(self, country, proxy=True, uc=True, headless=False):
        self.country = country
        self.cfg = COUNTRY_CONFIG[country]
        self.sb = None

        # 1. Install & lock correct chromedriver before SB touches it
        _ensure_chromedriver_147()

        # 2. Clean any lock files from previous interrupted runs
        user_data_dir = f"browser_sessions/{country}_session"
        os.makedirs(user_data_dir, exist_ok=True)
        _clear_chrome_locks(user_data_dir)

        browser_params = {
            "uc": uc,
            "headless2": headless,
            # user_data_dir for session persistence; also disables incognito
            # (incognito=True is incompatible with uc=True in SB 4.47.x)
            "user_data_dir": user_data_dir,
            "proxy": PROXY if proxy else None,
            # --no-sandbox required on Linux (Ubuntu 22.04 kernel 6.8+ enforces
            # AppArmor user-namespace restrictions; Chrome crashes without it).
            # Not needed / not valid on Windows.
            "chromium_arg": (
                "--no-sandbox,--disable-setuid-sandbox,"
                "--disable-gpu,--disable-software-rasterizer,--disable-dev-shm-usage"
                if _IS_LINUX else
                "--disable-gpu,--disable-software-rasterizer"
            ),
        }
        self._sb_ctx = SB(**browser_params)

    def __enter__(self):
        print(f"[BrowserClient] Starting Chrome (country={self.country})...", flush=True)
        self.sb = self._sb_ctx.__enter__()
        print("[BrowserClient] Chrome started.", flush=True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sb_ctx.__exit__(exc_type, exc_val, exc_tb)

    def open_login_page(self):
        url = f"https://visa.vfsglobal.com/gbr/en/{self.country}/login"
        print(f"[BrowserClient] Opening: {url}", flush=True)
        self.sb.activate_cdp_mode(url)
        print("[BrowserClient] Login page loaded.", flush=True)

    def handle_cookies(self):
        try:
            self.sb.wait_for_element("#onetrust-accept-btn-handler", timeout=100)
            self.sb.driver.uc_click("#onetrust-accept-btn-handler")
            print("[BrowserClient] Cookie banner accepted.", flush=True)
        except TimeoutException:
            print("[BrowserClient] No cookie banner within timeout.", flush=True)

    def solve_captcha(self):
        try:
            self.sb.uc_gui_click_captcha()
            self.sb.uc_click('button:contains("Submit")')
        except Exception:
            pass

    def check_is_ip_blocked(self):
        try:
            self.sb.wait_for_element('div[role="alert"].alert-info', timeout=10)
            print("[BrowserClient] IP block alert detected.", flush=True)
            return True
        except Exception:
            pass
        try:
            self.sb.assert_text("Sorry, we've been unable to progress", "h1", timeout=4)
            print("[BrowserClient] IP block detected via heading.", flush=True)
            return True
        except Exception:
            return False

    def get_auth_token(self):
        return self.sb.execute_script("return sessionStorage.getItem('JWT');")

    def switch_tabs(self):
        self.sb.click("#mat-select-0", scroll=True)
        self.sb.sleep(2)
        self.sb.cdp.gui_click_element("#NAKH")
        self.sb.sleep(1)
        self.sb.click("#mat-select-0", scroll=True)
        self.sb.sleep(1)
        self.sb.cdp.gui_click_element("#NAKN")
        self.sb.sleep(2)
        self.sb.click("#mat-select-1", scroll=True)
        self.sb.sleep(2)
        self.sb.cdp.gui_click_element("#TA")
        self.solve_captcha()

    def call_check_slot(self, login_user: str, jwt_token: str):
        cfg = self.cfg
        js_code = f"""
        const done = arguments[0];
        fetch('https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable', {{
          method: 'POST',
          headers: {{
            'accept': 'application/json, text/plain, */*',
            'content-type': 'application/json;charset=UTF-8',
            'authorize': {json.dumps(jwt_token)},
            'route': {json.dumps(cfg['route'])}
          }},
          credentials: 'include',
          body: JSON.stringify({{
            countryCode: {json.dumps(cfg['countryCode'])},
            missionCode: {json.dumps(cfg['missionCode'])},
            vacCode: {json.dumps(cfg['vacCode'])},
            visaCategoryCode: {json.dumps(cfg['visaCategoryCode'])},
            roleName: 'Individual',
            loginUser: {json.dumps(login_user)},
            payCode: ''
          }})
        }})
        .then(async r => {{
          const text = await r.text();
          done(JSON.stringify({{ status: r.status, statusText: r.statusText,
            cfRay: r.headers.get('cf-ray'), body: text }}));
        }})
        .catch(e => done(JSON.stringify({{ error: e && e.message ? e.message : String(e) }})));
        """
        raw = self.sb.execute_async_script(js_code)
        result = json.loads(raw) if isinstance(raw, str) else raw
        try:
            result["body"] = json.loads(result["body"])
        except (ValueError, TypeError):
            pass
        return result