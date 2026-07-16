# browser_client.py
import base64
import json
import os
import platform
import re
import secrets
import socket
import string
import threading

from seleniumbase import SB
from seleniumbase.core import proxy_helper as _sb_proxy_helper

from booking_flow import BookingFlow
from config import APPLICANT_CONFIG, COUNTRY_CONFIG, PROXY_CONFIG

# Linux-only: on dev machines where this runs inside a terminal launched from
# the VSCode snap, the shell carries GTK/GIO env vars pointing at the snap's
# own confined module cache (GIO_MODULE_DIR etc.) plus XDG_SESSION_TYPE=
# wayland. Non-headless Chrome inherits these, its GTK UI init tries to load
# an incompatible Qt-based GIO module from the snap, and it aborts with a Qt
# "no platform plugin" SIGABRT before the CDP debugger port ever comes up —
# this is what "chrome crashed with SIGABRT" / "cannot connect to chrome at
# 127.0.0.1:9222" actually is. Headless Chrome never hits this path (no GTK
# UI init), and it doesn't happen on Windows at all (no GTK/X11), hence the
# platform check. xvfb=True (below, in BrowserClient) gives each session its
# own virtual display, but that alone doesn't fix this — the crash is from
# inherited env vars, not from which display Chrome uses.
if platform.system() == "Linux":
    for _var in (
        "GIO_MODULE_DIR",
        "GTK_PATH",
        "GTK_EXE_PREFIX",
        "GTK_IM_MODULE_FILE",
        "GTK_MODULES",
        "XDG_SESSION_TYPE",
        "XDG_SESSION_DESKTOP",
        "XDG_SESSION_CLASS",
        "GIO_LAUNCHED_DESKTOP_FILE",
        "GIO_LAUNCHED_DESKTOP_FILE_PID",
    ):
        os.environ.pop(_var, None)

# Our residential proxy provider (VFS_PROXY_URL) encodes a sticky-session id
# in the proxy username, e.g. "...-session-av7gkchx-country-gb-rotation-0" —
# every connection presenting that exact username gets handed the *same*
# exit IP for as long as that id is reused. VFS_PROXY_URL hardcodes one
# fixed id, so left alone, every BrowserClient (every country, every check)
# was sharing one exit IP. Replacing it with a fresh random id per
# BrowserClient gets each browser launch its own IP from the provider
# instead — see README.md's "Proxy" section.
_PROXY_SESSION_RE = re.compile(r"session-[A-Za-z0-9]+")


def _randomize_proxy_session(username: str) -> str:
    new_id = "session-" + "".join(
        secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8)
    )
    new_username, count = _PROXY_SESSION_RE.subn(new_id, username)
    # No "session-..." segment in this username at all (e.g. a differently
    # configured provider) — nothing to randomize, leave it untouched.
    return new_username if count else username


class LocalAuthProxy:
    """
    Minimal local HTTP/HTTPS forwarding proxy that injects Proxy-Authorization.

    Chrome connects to 127.0.0.1:<local_port> with no credentials.
    This proxy adds the auth header and forwards every request to the real
    upstream proxy.  Chrome never receives a 407, so no auth dialog appears.
    """

    def __init__(self, remote_host: str, remote_port: int, username: str, password: str):
        self.remote_host = remote_host
        self.remote_port = remote_port
        self._auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode()
        ).decode()
        self._server: socket.socket | None = None
        self.local_port: int = 0

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self.local_port = self._server.getsockname()[1]
        self._server.listen(20)
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        print(f"[LocalAuthProxy] Listening on 127.0.0.1:{self.local_port} → "
              f"{self.remote_host}:{self.remote_port}")

    def stop(self):
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass

    def _accept_loop(self):
        while True:
            try:
                client, _ = self._server.accept()
                threading.Thread(
                    target=self._handle, args=(client,), daemon=True
                ).start()
            except Exception:
                break

    @staticmethod
    def _relay(src: socket.socket, dst: socket.socket):
        try:
            while True:
                data = src.recv(8192)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except Exception:
                    pass

    def _handle(self, client: socket.socket):
        try:
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = client.recv(4096)
                if not chunk:
                    return
                raw += chunk

            sep = raw.index(b"\r\n\r\n")
            headers_str = raw[:sep].decode("utf-8", errors="ignore")
            body = raw[sep + 4:]

            lines = headers_str.split("\r\n")
            first_line = lines[0]

            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.connect((self.remote_host, self.remote_port))

            # Strip any existing auth header, inject ours
            kept = [l for l in lines[1:] if not l.lower().startswith("proxy-authorization")]
            kept.append(f"Proxy-Authorization: {self._auth}")

            if first_line.upper().startswith("CONNECT"):
                out = "\r\n".join([first_line] + kept) + "\r\n\r\n"
                remote.sendall(out.encode())
                # Wait for upstream 200 Connection established
                resp = b""
                while b"\r\n\r\n" not in resp:
                    resp += remote.recv(4096)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                threading.Thread(
                    target=self._relay, args=(remote, client), daemon=True
                ).start()
                self._relay(client, remote)
            else:
                out = "\r\n".join([first_line] + kept) + "\r\n\r\n"
                remote.sendall(out.encode() + body)
                self._relay(remote, client)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Monkey-patch SeleniumBase's proxy extension creator to emit MV2 instead of MV3.
#
# Root cause: proxy_helper.create_proxy_ext() writes:
#   "manifest_version": 3, "background": {"service_worker": "background.js"}
# Chrome MV3 service workers are async — they cannot use synchronous ["blocking"]
# webRequest listeners. The listener silently does nothing, so Chrome shows the
# native proxy-auth dialog instead of handling credentials automatically.
#
# MV2 persistent background pages ARE allowed to use ["blocking"], which lets
# the extension intercept the 407 and return credentials with no dialog.
# Locally-loaded MV2 extensions continue to work in all current Chrome versions;
# the MV2 phase-out only affects the Chrome Web Store.
# ---------------------------------------------------------------------------
_original_create_proxy_ext = _sb_proxy_helper.create_proxy_ext

_MV2_MANIFEST = """{
  "version": "1.0.0",
  "manifest_version": 2,
  "name": "Chrome Proxy",
  "permissions": [
    "proxy",
    "tabs",
    "unlimitedStorage",
    "storage",
    "webRequest",
    "webRequestBlocking",
    "<all_urls>"
  ],
  "background": {
    "scripts": ["background.js"],
    "persistent": true
  },
  "minimum_chrome_version": "88.0.0"
}"""


def _mv2_create_proxy_ext(
    proxy_string, proxy_user, proxy_pass,
    proxy_scheme="http", bypass_list=None, zip_it=True,
):
    # Let SB create the directory and write initial files.
    _original_create_proxy_ext(
        proxy_string, proxy_user, proxy_pass, proxy_scheme, bypass_list, zip_it
    )

    ext_dir = _sb_proxy_helper.PROXY_DIR_PATH
    if not os.path.exists(ext_dir):
        return

    # Overwrite manifest.json: MV3 service workers cannot use ["blocking"] listeners.
    with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
        f.write(_MV2_MANIFEST)

    # Overwrite background.js: SB's version only calls chrome.proxy.settings.set()
    # with scope="regular", so in incognito Chrome uses --proxy-server instead and
    # the extension doesn't "own" that proxy → onAuthRequired never reaches the
    # listener → auth dialog appears.  Adding scope="incognito_persistent" makes the
    # extension own the proxy in incognito too, so the listener fires correctly.
    clean = proxy_string
    proto = "http"
    if "://" in proxy_string:
        proto, clean = proxy_string.split("://", 1)
    host = clean.split(":")[0]
    port = clean.split(":")[1]
    bypass = bypass_list or ""

    background_js = (
        'var config = {\n'
        '    mode: "fixed_servers",\n'
        '    rules: {\n'
        '      singleProxy: {\n'
        f'        scheme: "{proxy_scheme}",\n'
        f'        host: "{host}",\n'
        f'        port: parseInt("{port}")\n'
        '      },\n'
        f'    bypassList: ["{bypass}"]\n'
        '    }\n'
        '  };\n'
        'chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});\n'
        # Also claim the proxy for incognito so onAuthRequired fires through us there too.
        'chrome.proxy.settings.set({value: config, scope: "incognito_persistent"}, function() {});\n'
        'function callbackFn(details) {\n'
        '    return {\n'
        '        authCredentials: {\n'
        f'            username: "{proxy_user}",\n'
        f'            password: "{proxy_pass}"\n'
        '        }\n'
        '    };\n'
        '}\n'
        'chrome.webRequest.onAuthRequired.addListener(\n'
        '        callbackFn,\n'
        '        {urls: ["<all_urls>"]},\n'
        "        ['blocking']\n"
        ');'
    )

    with open(os.path.join(ext_dir, "background.js"), "w") as f:
        f.write(background_js)


_sb_proxy_helper.create_proxy_ext = _mv2_create_proxy_ext


class BrowserClient:
    def __init__(self, country, proxy=True, uc=True, headless=False):
        self.country = country
        self.sb = None
        self._config = COUNTRY_CONFIG[country]
        # VFS account email for the active country — used as `loginUser` when
        # registering on a mission's appointment waiting list, and to look up
        # the right APPLICANT_CONFIG entry when booking.
        self.login_user: str | None = None
        # FrontendBridge instance (set by main.py) — routes booking_flow.py's
        # human decision points (date/time/review/payment) to the dashboard
        # instead of a blocking terminal input().
        self.bridge = None

        print("[BrowserClient] ── Initialising BrowserClient ──")
        print(f"[BrowserClient]   country   : {country}")
        print("[BrowserClient]   UC mode   : True  (undetected Chrome, bypasses Cloudflare)")
        print("[BrowserClient]   incognito : True (always on — no leftover cookies/session state between runs)")
        print(f"[BrowserClient]   headless  : {headless}")
        print(f"[BrowserClient]   proxy     : {'enabled (local-forward)' if proxy else 'disabled'}")

        chromium_args = [
            "--disable-software-rasterizer",
            "--disable-dev-shm-usage",
            # Required when Chrome runs as root (e.g. on a Linux server).
            "--no-sandbox",
        ]

        browser_params = {
            "uc": True,
            "incognito": True,
            "headless2": headless,
            "chromium_arg": ",".join(chromium_args),
            # On Linux servers DISPLAY is set externally (Xvfb started by the
            # systemd ExecStartPre). On local Linux the real X display is used.
            # xvfb=True told SeleniumBase to spawn its own per-session Xvfb,
            # but its sbvirtualdisplay management fails on some Ubuntu 24.04
            # server configurations — removed in favour of the pre-started Xvfb.
        }

        self._local_proxy: LocalAuthProxy | None = None
        if proxy:
            proxy_str = PROXY_CONFIG["proxy"]
            creds, host_port = proxy_str.rsplit("@", 1)
            username, password = creds.split(":", 1)
            username = _randomize_proxy_session(username)
            remote_host, remote_port = host_port.rsplit(":", 1)
            session_match = _PROXY_SESSION_RE.search(username)
            print(f"[BrowserClient]   remote proxy: {remote_host}:{remote_port}")
            if session_match:
                print(f"[BrowserClient]   proxy {session_match.group()} (fresh exit IP for this launch)")
            # Start local forwarding proxy — Chrome connects to localhost with no
            # auth, LocalAuthProxy injects credentials and forwards to the real proxy.
            # This eliminates the native proxy-auth dialog entirely.
            self._local_proxy = LocalAuthProxy(
                remote_host, int(remote_port), username, password
            )
            self._local_proxy.start()
            browser_params["proxy"] = f"127.0.0.1:{self._local_proxy.local_port}"
        else:
            print("[BrowserClient] No proxy — connecting directly.")

        print("[BrowserClient] Final browser_params:")
        for k, v in browser_params.items():
            if k == "chromium_arg":
                for flag in v.split(","):
                    print(f"[BrowserClient]   chromium_arg → {flag.strip()}")
            elif k == "proxy":
                host = v.rsplit("@", 1)[-1]
                print(f"[BrowserClient]   proxy = ***@{host}")
            else:
                print(f"[BrowserClient]   {k} = {v}")

        print("[BrowserClient] Calling SB() to create browser context...")
        self._sb_ctx = SB(**browser_params)
        print("[BrowserClient] SB context created. Browser will launch on __enter__.")

    def __enter__(self):
        print("[BrowserClient] __enter__ — launching Chrome...")
        self.sb = self._sb_ctx.__enter__()
        print("[BrowserClient] Chrome launched successfully.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print("[BrowserClient] __exit__ — closing browser...")
        self._sb_ctx.__exit__(exc_type, exc_val, exc_tb)
        if self._local_proxy:
            self._local_proxy.stop()
        print("[BrowserClient] Browser closed.")

    def switch_country(self, new_country: str) -> None:
        """
        Update the active country and wipe all browser state so the previous
        country's VFS session cannot bleed into the new one.
        """
        self.country = new_country
        self._config = COUNTRY_CONFIG[new_country]
        print(f"[BrowserClient] Switching to {new_country.upper()} — clearing session...")
        self._log_state(f"Before clearing session (switching to {new_country.upper()})")

        # Wipe localStorage and sessionStorage *while still on the
        # visa.vfsglobal.com origin*. The Angular SPA persists state there
        # (e.g. the last-visited mission code) — if we navigate to about:blank
        # first, clear() runs against about:blank's origin instead and is a
        # no-op, so the stale mission state survives and the new country's
        # /dashboard route silently redirects back to the previous country.
        try:
            self.sb.execute_script(
                "try { window.localStorage.clear(); } catch(e) {} "
                "try { window.sessionStorage.clear(); } catch(e) {}"
            )
        except Exception:
            pass

        # Wipe cookies (all domains)
        try:
            self.sb.driver.delete_all_cookies()
        except Exception:
            pass

        # Now navigate to a neutral page so no leftover Angular app/router
        # state remains in memory before the next login flow begins.
        try:
            self.sb.open("about:blank")
        except Exception:
            pass

        self._log_state(f"After clearing session — ready for {new_country.upper()}")
        print(f"[BrowserClient] Session cleared. Ready for {new_country.upper()} login.")

    def open_login_page(self):
        # Visit the country home page first to establish the VFS session cookie.
        # Navigating directly to /login on some missions (e.g. DNK) triggers an
        # Angular "Session Expired" guard because no session cookie exists yet.
        home_url = f"https://visa.vfsglobal.com/gbr/en/{self.country}/"
        print(f"[BrowserClient] Warming up session via home page: {home_url}")
        self.sb.open(home_url)
        self.sb.sleep(3)
        self._log_state("After opening home page")

        url = f"https://visa.vfsglobal.com/gbr/en/{self.country}/login"
        print(f"[BrowserClient] Opening login page: {url}")
        self.sb.open(url)
        self.sb.sleep(5)
        try:
            self.sb.wait_for_element("#email", timeout=15)
            print("[BrowserClient] Login form ready (#email found).")
        except Exception:
            print("[BrowserClient] #email not found after 15s — sleeping 10s more...")
            self.sb.sleep(10)
        self._log_state("After opening login page")
        print("[BrowserClient] Login page loaded.")

    def handle_cookies(self):
        try:
            self.sb.wait_for_element("#onetrust-accept-btn-handler", timeout=15)
            self.sb.driver.uc_click("#onetrust-accept-btn-handler")
        except Exception:
            # Banner may already be dismissed or may not appear — safe to continue.
            print("[BrowserClient] No cookie banner found, continuing.")

    def _cloudflare_dialog_present(self, timeout: float = 2) -> bool:
        try:
            self.sb.wait_for_element("app-cloudflare-dialog", timeout=timeout)
            return True
        except Exception:
            return False

    def solve_captcha(self, max_attempts: int = 3) -> bool:
        """
        Click through the login-flow Cloudflare captcha if one is actually
        showing, verifying the dialog is gone afterward instead of trusting
        whether uc_gui_click_captcha() raised. That click (and the
        click-Submit that used to follow it unconditionally) routinely
        raises even when the captcha is solved — e.g. Cloudflare's
        Turnstile often self-submits with no separate Submit button to
        click, so the old code's blind `except Exception: print("Could not
        solve captcha")` was a false negative almost every time the
        dialog auto-passed on the real screen. Retries up to max_attempts
        only while the dialog is still genuinely present; returns whether
        it ended up gone.
        """
        if not self._cloudflare_dialog_present():
            print("[BrowserClient] No captcha modal present — nothing to solve.")
            return True

        for attempt in range(1, max_attempts + 1):
            print(f"[BrowserClient] Captcha modal present — solving (attempt {attempt}/{max_attempts})...")
            try:
                self.sb.uc_gui_click_captcha()
            except Exception as e:
                print(f"[BrowserClient]   click raised {type(e).__name__}: {e} — checking dialog state anyway.")
            # Not every captcha flow has a separate Submit button (Turnstile
            # often self-submits) — try it, but its absence isn't a failure.
            try:
                self.sb.uc_click('button:contains("Submit")', timeout=3)
            except Exception:
                pass
            self.sb.sleep(2)
            if not self._cloudflare_dialog_present():
                print(f"[BrowserClient] Captcha solved (confirmed gone after attempt {attempt}).")
                return True

        print(f"[BrowserClient] Captcha modal still present after {max_attempts} attempts — giving up.")
        return False

    def check_is_ip_blocked(self):
        try:
            self.sb.wait_for_element('div[role="alert"].alert-info', timeout=10)
            print("[BrowserClient] IP block alert detected.")
            return True
        except Exception:
            print("[BrowserClient] No IP block alert.")

        try:
            self.sb.assert_text("Sorry, we've been unable to progress", "h1", timeout=4)
            print("[BrowserClient] IP block detected via heading.")
            return True
        except Exception as e:
            print("[BrowserClient] No IP block via heading:", e)

        # Some block variants land on .../page-not-found with the message
        # only in <title>, no visible h1 at all (seen for real: Denmark's
        # session landed here after submitting credentials and the heading
        # check above missed it, so the run just timed out 50s later
        # waiting for an OTP field that was never going to appear).
        try:
            title = self.sb.get_title()
            if "unable to progress" in title.lower():
                print(f"[BrowserClient] IP block detected via page title: {title!r}")
                return True
        except Exception as e:
            print("[BrowserClient] No IP block via title:", e)
        return False

    def get_auth_token(self):
        return self.sb.execute_script("return sessionStorage.getItem('JWT');")

    def _log_state(self, action: str) -> None:
        """
        Log a description of the action being performed together with the
        browser's current URL and page title — gives full visibility into
        what the automation is doing and where it currently is in the
        browser, for diagnosing session/redirect issues.
        """
        try:
            url = self.sb.get_current_url()
        except Exception as e:
            url = f"<unknown: {type(e).__name__}: {e}>"
        try:
            title = self.sb.get_title()
        except Exception as e:
            title = f"<unknown: {type(e).__name__}: {e}>"
        self._log(f"  [STATE] {action} | url={url} | title={title!r}")

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

    # ------------------------------------------------------------------
    # UI-based booking form — helpers
    # ------------------------------------------------------------------

    def _navigate_to_booking_form(self):
        """
        Navigate dashboard → click 'Start New Booking' → land on /application-detail.

        Why not navigate directly to /application-detail?
        The Angular route guard on that page requires coming from /dashboard via the
        'Start New Booking' button.  Direct URL navigation causes the guard to redirect
        to /login, destroying the session.  Always use this method to reach the form.
        """
        dashboard_url = f"https://visa.vfsglobal.com/gbr/en/{self.country}/dashboard"
        self._log(f"Dashboard → Start New Booking  ({dashboard_url})")
        self.sb.open(dashboard_url)

        # Angular SPAs need extra time to bootstrap and render the dashboard components.
        self.sb.sleep(5)
        current_url = self.sb.get_current_url()
        page_title = self.sb.get_title()
        self._log(f"  Page title: {page_title}")
        self._log(f"  Current URL: {current_url}")

        # Session expired — VFS redirects the dashboard URL to /login, appends a
        # 401 to the URL, sends us to /page-not-found (its rate-limit/expired-
        # session error page), or renders a "session expired" message after too
        # many requests. Raise immediately so the caller can re-authenticate
        # rather than accidentally clicking the login submit button (which is
        # also button.mat-btn-lg).
        current_url_lower = current_url.lower()
        page_title_lower = page_title.lower()
        session_expired = (
            "/login" in current_url_lower
            or "401" in current_url_lower
            or "page-not-found" in current_url_lower
            or "401" in page_title_lower
            or "page not found" in page_title_lower
        )
        if not session_expired:
            try:
                page_source_lower = self.sb.get_page_source().lower()
                if "session expired" in page_source_lower or "page not found" in page_source_lower:
                    session_expired = True
            except Exception:
                pass

        if session_expired:
            raise RuntimeError(
                f"SESSION_EXPIRED: dashboard redirected to {current_url!r} "
                "(/login, 401, or page-not-found) — VFS session has expired "
                "or rate-limited the request, re-authentication is required."
            )

        # Attempt 1: original mat-btn-lg selector with a generous timeout
        clicked = False
        for selector in [
            "button.mat-btn-lg",
            "a.mat-btn-lg",
            "button.mat-raised-button",
            "button[color='primary']",
            "button.mat-flat-button",
            ".dashboard button",
            "app-dashboard button",
        ]:
            try:
                self.sb.wait_for_element(selector, timeout=5)
                self._log(f"  'Start New Booking' found via: {selector}")
                self.sb.driver.uc_click(selector)
                clicked = True
                break
            except Exception:
                pass

        # Attempt 2: JS – find any button whose visible text contains "New Booking"
        if not clicked:
            self._log("  CSS selectors failed — trying JS text-based button search...")
            try:
                self.sb.execute_script("""
                    var buttons = Array.from(document.querySelectorAll('button, a'));
                    var btn = buttons.find(function(b) {
                        return b.innerText && b.innerText.toLowerCase().indexOf('new booking') !== -1;
                    });
                    if (btn) { btn.click(); }
                    else { throw new Error('Start New Booking button not found via JS'); }
                """)
                clicked = True
                self._log("  Clicked via JS text search.")
            except Exception as e:
                self._log(f"  JS text search failed: {e}")

        # Attempt 3: XPath text match
        if not clicked:
            self._log("  Trying XPath text match...")
            try:
                from selenium.webdriver.common.by import By
                btn = self.sb.driver.find_element(
                    By.XPATH,
                    "//*[self::button or self::a][contains(translate(., "
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                    "'new booking')]"
                )
                btn.click()
                clicked = True
                self._log("  Clicked via XPath.")
            except Exception as e:
                self._log(f"  XPath failed: {e}")

        if not clicked:
            # Log all buttons on the page to help diagnose
            try:
                all_buttons = self.sb.execute_script(
                    "return Array.from(document.querySelectorAll('button, a')).map("
                    "function(b){ return b.className + ' | ' + (b.innerText||'').trim().slice(0,60); }"
                    ");"
                )
                self._log("  All clickable elements on page:")
                for b in (all_buttons or [])[:30]:
                    self._log(f"    {b}")
            except Exception:
                pass
            raise RuntimeError(
                "Could not find 'Start New Booking' button on dashboard after all attempts."
            )

        # Give Angular time to transition to /application-detail and render the form.
        self.sb.sleep(8)
        try:
            self.sb.wait_for_element("#application-detail, mat-select", timeout=10)
        except Exception:
            pass
        self._log("Form ready (#application-detail).")
        self._log_state("After clicking 'Start New Booking'")

    def _ensure_on_form(self):
        """
        Confirm the form (#mat-select-0) is present.  If not (e.g. Angular pushed
        us away after a submission), re-navigate through the dashboard.
        """
        try:
            self.sb.wait_for_element("#mat-select-0", timeout=5)
        except Exception:
            self._log("  Form lost — re-navigating via dashboard...")
            self._navigate_to_booking_form()

    def _select_mat_option(self, select_id: str, option_id: str):
        """
        Open a mat-select dropdown and click the option whose element id matches option_id.
        Uses [id="..."] attribute selector so IDs with spaces (e.g. 'BUL VISA') work correctly.
        CSS #id selector breaks on spaces; attribute selector handles any character.
        Retries once if the panel does not appear on the first attempt.
        """
        from selenium.webdriver.common.keys import Keys

        self._log(f"  Dropdown #{select_id}  →  option '{option_id}'")
        panel_sel = f'#{select_id}-panel mat-option[id="{option_id}"]'

        for attempt in range(2):
            try:
                self.sb.driver.uc_click(f"#{select_id}")
                self.sb.sleep(1.5)
                # CDK overlay panel: id="mat-select-{N}-panel"
                self.sb.wait_for_element(panel_sel, timeout=15)
                self.sb.driver.uc_click(panel_sel)
                self.sb.sleep(1)
                self._log_state(f"After selecting #{select_id} → '{option_id}'")
                return
            except Exception as exc:
                if attempt == 0:
                    self._log(f"  Dropdown #{select_id} attempt 1 failed ({exc}). "
                              "Closing panel and retrying...")
                    # Close any stuck overlay before retrying
                    try:
                        self.sb.find_element("body").send_keys(Keys.ESCAPE)
                        self.sb.sleep(1)
                    except Exception:
                        pass
                    self.sb.sleep(2)
                else:
                    raise

    def _read_mat_options(self, select_id: str) -> list:
        """
        Open a mat-select, read every available option, then close without selecting.
        Returns [{"id": "...", "text": "..."}, ...]
        """
        from selenium.webdriver.common.keys import Keys

        options = []
        try:
            self.sb.driver.uc_click(f"#{select_id}")
            self.sb.sleep(1.5)
            panel_sel = f"#{select_id}-panel"
            self.sb.wait_for_element(panel_sel, timeout=10)
            for el in self.sb.find_elements(f"{panel_sel} mat-option"):
                opt_id   = el.get_attribute("id")
                opt_text = el.text.strip()
                if opt_id and opt_text:
                    options.append({"id": opt_id, "text": opt_text})
            # Press Escape to close panel without selecting anything
            self.sb.find_element("body").send_keys(Keys.ESCAPE)
            self.sb.sleep(0.5)
        except Exception as e:
            self._log(f"  _read_mat_options(#{select_id}): {e}")
        return options

    def _submit_and_read_result(self) -> tuple[str, str]:
        """
        Handle the captcha modal, click Submit, and return (result, slot_details).
        result:      "slots_available" | "no_slots" | "error"
        slot_details: text from the info banner, e.g.
                      "Earliest available slot for 1 Applicants is : 10-06-2026"
        """
        try:
            self.sb.wait_for_element("app-cloudflare-dialog", timeout=10)
            self._log("  Captcha modal appeared — solving...")
            self.sb.uc_gui_click_captcha()
            self.sb.sleep(3)
            self._log("  Captcha solved — clicking Submit...")
            self.sb.driver.uc_click("app-cloudflare-dialog button.mat-btn-lg")
            self.sb.sleep(3)
        except Exception as e:
            self._log(f"  No captcha modal (auto-passed or not required) — {type(e).__name__}: {e}")

        self._log_state("After submit / captcha handling")

        # Covers every banner style VFS has been observed to use for both
        # the slots-available and no-slots-available states — the "Sorry,
        # no appointment slots..." message doesn't always carry the
        # `.Information` class, just `role="alert"` (or no special class at
        # all), so the generic role selector is the one that actually fires.
        banner_selectors = (
            'div[role="alert"].Information',
            ".Information.alert",
            "div.form-info",
            ".Information .alert",
            'div[role="alert"]',
            ".alert-danger",
            ".alert-warning",
            ".alert-info",
        )

        def _read_info_banner() -> str:
            """Try every known selector for the result banner and return its text."""
            for sel in banner_selectors:
                els = self.sb.find_elements(sel)
                if els:
                    text = els[0].text.strip()
                    if text:
                        return text
            return ""

        # Wait for the result banner (covers both slots-available and no-slots states)
        try:
            self.sb.wait_for_element(", ".join(banner_selectors), timeout=12)
            slot_details = _read_info_banner()
            self._log(f"  Info banner: {slot_details!r}")
            lowered = slot_details.lower()
            if "earliest available" in lowered:
                return "slots_available", slot_details
            if slot_details:
                return "no_slots", slot_details
        except Exception as e:
            self._log(f"  No result banner within timeout — {type(e).__name__}: {e}")

        # Fallback: no recognized banner element — check for an enabled
        # proceed button, which only appears when slots are available.
        try:
            self.sb.wait_for_element("button.mat-btn-lg:not([disabled])", timeout=8)
            slot_details = _read_info_banner()
            return "slots_available", slot_details
        except Exception as e:
            self._log(f"  No enabled proceed button either — {type(e).__name__}: {e}")

        # Last resort: the banner text wasn't caught by any selector above —
        # scan the raw page source for VFS's known "no slots" phrasing so a
        # missed CSS selector is reported as no_slots, not a false error.
        try:
            page_text = self.sb.get_page_source().lower()
            if "no appointment slots" in page_text or "sorry but no" in page_text:
                return "no_slots", "No appointment slots are currently available."
        except Exception as e:
            self._log(f"  get_page_source() fallback failed — {type(e).__name__}: {e}")

        self._log("  _submit_and_read_result: no banner, no proceed button, no recognizable page text — returning error.")
        return "error", ""

    # ------------------------------------------------------------------
    # Interactive booking — "Your Details" through payment
    # ------------------------------------------------------------------

    def book_appointment(self) -> bool:
        """
        After a 'slots_available' result on the Application Detail page,
        drive the full booking flow (applicant form -> OTP -> captcha ->
        date/time -> review -> payment) via BookingFlow. Every human
        decision point — including the applicant-form fields themselves,
        which BookingFlow fetches live from the page rather than from a
        static config — is routed through the dashboard via `self.bridge`
        instead of a blocking terminal input() call.

        Returns True if the flow completed (payment submitted), False if
        it couldn't start (no bridge) or was cancelled.
        """
        if not self.bridge:
            self._log("  No dashboard bridge attached — cannot run booking flow.")
            return False
        if not self.login_user:
            self._log("  No login_user set — cannot run booking flow.")
            return False

        flow = BookingFlow(self, self.bridge, self.login_user)
        try:
            return bool(flow.run())
        except Exception as e:
            self._log(f"  Booking flow error: {e}")
            return False

    # ------------------------------------------------------------------
    # Exhaustive combination scan
    # ------------------------------------------------------------------

    @staticmethod
    def _log(msg: str):
        """Print a timestamped log line."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}")

    def check_all_combinations(self, delay_secs: int = 5) -> list:
        """
        Dynamically discover every option in all three dropdowns, build the
        full Cartesian product of combinations, test each one, and return
        the results.

        Discovery strategy
        ------------------
        1. Open a fresh form, read centre options (mat-select-0), close.
        2. For each centre: open fresh form, select that centre, read
           appointment-category options (mat-select-2), close.
           While here, also read sub-category options (mat-select-1) after
           selecting the first appointment category — sub-categories are
           the same set for a given centre regardless of category.
        3. Build every (centre × appt_cat × sub_cat) triple.

        Testing
        -------
        For each triple: open a fresh form, fill all three dropdowns,
        handle captcha, submit, read result.
        A 'delay_secs' pause is inserted between each test.

        Returns list of dicts:
            {
              "combo": int,          # 1-based index
              "total": int,
              "centre":   {"id": ..., "text": ...},
              "appt_cat": {"id": ..., "text": ...},
              "sub_cat":  {"id": ..., "text": ...},
              "result":   "slots_available" | "no_slots" | "error",
            }
        """
        # ── Phase 1: DISCOVERY  (one navigation, stay on page for all centres) ──────
        self._log("=" * 55)
        self._log("DISCOVERY PHASE — reading all dropdown options")
        self._log("=" * 55)

        # ONE navigation through dashboard → application-detail.
        # We stay on this page for the entire discovery phase; Angular resets
        # downstream dropdowns automatically when mat-select-0 changes.
        self._navigate_to_booking_form()

        self._log("Reading Application Centre options (mat-select-0)...")
        centre_options = self._read_mat_options("mat-select-0")
        self._log(f"Found {len(centre_options)} centre(s): "
                  f"{[c['text'] for c in centre_options]}")

        # TESTING: London + Edinburgh centres are in scope — skip Manchester, etc.
        centre_options = [
            c for c in centre_options
            if "london" in c["text"].lower() or "edinburgh" in c["text"].lower()
        ]
        self._log(f"Centre(s) in scope: {[c['text'] for c in centre_options]}")

        if not centre_options:
            self._log("ERROR: no London/Edinburgh centre options found — aborting.")
            return []

        appt_cats_by_centre: dict = {}
        sub_cats_by_centre: dict = {}

        for centre in centre_options:
            self._log(f"--- Centre: {centre['text']} ---")
            # Selecting a new centre resets mat-select-2 / mat-select-1 in Angular.
            self._select_mat_option("mat-select-0", centre["id"])
            self.sb.sleep(2)

            self._log("  Reading Appointment Category options (mat-select-2)...")
            cats = self._read_mat_options("mat-select-2")
            appt_cats_by_centre[centre["id"]] = cats
            self._log(f"  {len(cats)} category(ies): {[c['text'] for c in cats]}")

            if cats:
                # Select first category to reveal the sub-category dropdown (mat-select-1)
                self._select_mat_option("mat-select-2", cats[0]["id"])
                self.sb.sleep(2)
                self._log("  Reading Sub-category options (mat-select-1)...")
                sub_cats = self._read_mat_options("mat-select-1")
                sub_cats_by_centre[centre["id"]] = sub_cats
                self._log(f"  {len(sub_cats)} sub-cat(s): "
                          f"{[s['text'] for s in sub_cats]}")
            else:
                sub_cats_by_centre[centre["id"]] = []

        # ── Build combinations ──────────────────────────────────────────────────────
        all_combos = []
        for centre in centre_options:
            for appt_cat in appt_cats_by_centre.get(centre["id"], []):
                # -- ALL sub-categories (commented out — Tourist-only active below) --
                # for sub_cat in sub_cats_by_centre.get(centre["id"], []):
                #     all_combos.append((centre, appt_cat, sub_cat))

                # Tourist sub-category only
                for sub_cat in sub_cats_by_centre.get(centre["id"], []):
                    if sub_cat["text"].lower() in ("tourist", "tourism"):
                        all_combos.append((centre, appt_cat, sub_cat))

        total = len(all_combos)
        self._log("=" * 55)
        self._log(f"TESTING PHASE — {total} combination(s), {delay_secs}s between each")
        self._log("=" * 55)

        # ── Phase 2: TESTING  (re-navigate once per centre group) ───────────────────
        # We get a fresh form for each new Application Centre.  This resets Angular
        # form state cleanly and avoids the stale-dropdown issue that arises when a
        # captcha-solve or a previous result leaves the form in an unexpected state.
        # Within a centre group we stay on the same page and just change the
        # downstream dropdowns (appt_cat, sub_cat) without navigating.

        results = []
        current_centre_id: str | None = None

        for idx, (centre, appt_cat, sub_cat) in enumerate(all_combos, 1):
            self._log(f"[{idx}/{total}] '{centre['text']}'"
                      f" / '{appt_cat['text']}'"
                      f" / '{sub_cat['text']}'")
            try:
                # Navigate to a fresh form whenever the centre changes.
                if centre["id"] != current_centre_id:
                    self._log(f"  Centre changed → navigating to fresh form...")
                    self._navigate_to_booking_form()
                    current_centre_id = centre["id"]
                else:
                    # Same centre — guard against Angular pushing us off the page.
                    self._ensure_on_form()

                self._log(f"  SELECT Centre   → {centre['text']}")
                self._select_mat_option("mat-select-0", centre["id"])
                self.sb.sleep(3)  # extra time for Angular to load appt-category options

                self._log(f"  SELECT Appt Cat → {appt_cat['text']}")
                self._select_mat_option("mat-select-2", appt_cat["id"])
                self.sb.sleep(3)  # wait for sub-category options to load

                self._log(f"  SELECT Sub-cat  → {sub_cat['text']}")
                self._select_mat_option("mat-select-1", sub_cat["id"])
                self.sb.sleep(2)

                result, slot_details = self._submit_and_read_result()

            except RuntimeError as e:
                if "SESSION_EXPIRED" in str(e):
                    # Let this propagate — the caller restarts the whole scan for
                    # this country with a fresh session.
                    self._log(f"  SESSION EXPIRED mid-scan: {e}")
                    raise
                self._log(f"  EXCEPTION: {e}")
                result, slot_details = "error", ""
            except Exception as e:
                self._log(f"  EXCEPTION: {e}")
                result, slot_details = "error", ""

            label = ("*** SLOTS AVAILABLE ***" if result == "slots_available"
                     else ("no slots" if result == "no_slots" else "ERROR"))
            detail_suffix = f" — {slot_details}" if slot_details else ""
            self._log(f"  RESULT [{idx}/{total}]: {label}{detail_suffix}")
            results.append({
                "combo":       idx,
                "total":       total,
                "centre":      centre,
                "appt_cat":    appt_cat,
                "sub_cat":     sub_cat,
                "result":      result,
                "slot_details": slot_details,
            })

            # A slot is available — ask the dashboard whether to proceed to
            # booking before doing anything irreversible. If the user
            # declines (or there's no dashboard to ask), stop scanning this
            # country and let the caller move on to the next one.
            booking_initiated = False
            if result == "slots_available":
                print("\n" + "=" * 55)
                print(f"*** SLOT AVAILABLE: {centre['text']} / {appt_cat['text']} "
                      f"/ {sub_cat['text']} ***")
                if slot_details:
                    print(f"    {slot_details}")
                print("=" * 55)

                proceed = False
                if self.bridge:
                    self._log("  Asking dashboard whether to proceed to booking...")
                    answer = self.bridge.ask(
                        "confirm_booking",
                        {
                            "country": self.country,
                            "centre": centre["text"],
                            "appt_cat": appt_cat["text"],
                            "sub_cat": sub_cat["text"],
                            "slot_details": slot_details,
                        },
                    )
                    proceed = bool(answer and answer.get("confirmed"))
                else:
                    self._log("  No dashboard bridge attached — cannot ask, skipping booking.")

                if proceed:
                    try:
                        booking_initiated = self.book_appointment()
                    except Exception as e:
                        self._log(f"  Booking error: {e}")
                    results[-1]["booking_status"] = (
                        "initiated" if booking_initiated else "failed"
                    )
                else:
                    self._log("  Booking declined for this slot — moving to next country.")
                    results[-1]["booking_status"] = "skipped_by_user"

                # Either way, the decision for this country's scan is made —
                # don't keep testing further combinations.
                self._log("  Stopping combination scan for this country.")
                break

            if idx < total:
                self.sb.sleep(delay_secs)

        slots_found = [r for r in results if r["result"] == "slots_available"]

        # No direct slots anywhere — if this mission supports a waiting list,
        # register the configured applicant on it.
        if not slots_found and self._config.get("waitlist_enabled"):
            if not self.login_user:
                self._log("Waitlist enabled but no login_user set — skipping waitlist join.")
            else:
                self._log("No direct slots found — joining the waiting list...")
                try:
                    response = self.call_add_applicant()
                    status = response.get("status")
                    self._log(f"Waitlist join response: HTTP {status} — {response.get('body')}")
                    joined = status in (200, 201)
                    results.append({
                        "combo": total + 1,
                        "total": total,
                        "centre": {"id": "", "text": self._config["vacCode"]},
                        "appt_cat": {"id": "", "text": self._config["visaCategoryCode"]},
                        "sub_cat": {"id": "", "text": "Waitlist"},
                        "result": "waitlist_joined" if joined else "waitlist_failed",
                        "slot_details": f"HTTP {status}: {response.get('body')}",
                    })
                except Exception as e:
                    self._log(f"Waitlist join failed: {e}")

        self._log("=" * 55)
        self._log(f"SCAN COMPLETE — {len(slots_found)}/{total} combo(s) with slots available")
        if slots_found:
            for r in slots_found:
                details = r.get("slot_details", "")
                detail_suffix = f" | {details}" if details else ""
                self._log(
                    f"  *** AVAILABLE: {r['centre']['text']} / "
                    f"{r['appt_cat']['text']} / {r['sub_cat']['text']}"
                    + detail_suffix
                )
        self._log("=" * 55)
        return results

    # ------------------------------------------------------------------
    # Read-only slot check (no booking) — used by the public slot-checker
    # web page. Deliberately independent of check_all_combinations() /
    # book_appointment(): it never asks a human and never starts the
    # booking_flow.py state machine, so it can't interfere with the
    # existing human-in-the-loop booking pipeline.
    # ------------------------------------------------------------------

    def check_slot_only(
        self, centre_keyword: str = "london", sub_cat_keywords=("tourist", "tourism")
    ) -> dict:
        """
        Pick the first centre matching `centre_keyword` and its first
        appointment category. For the sub-category dropdown: if one option
        matches `sub_cat_keywords` (Tourist/Tourism), check only that one,
        same narrow scope as before. Some missions don't offer Tourism at
        all (e.g. only "Long Stay"/"Short Stay") — in that case there's no
        single right option to assume, so every sub-category on offer gets
        its own check instead of guessing or giving up.

        Returns a dict with "centre", "appt_cat", and "combos" — a list of
        {"sub_cat", "result", "slot_details"} dicts, one per sub-category
        checked (just one entry when Tourism was found). On a discovery
        failure (no matching centre, no categories, or no sub-categories at
        all) returns {"result": "error", "reason": ...} instead, with
        whichever of "centre"/"appt_cat" were already resolved.
        """
        self._navigate_to_booking_form()

        self._log("Reading Application Centre options (mat-select-0)...")
        centre_options = self._read_mat_options("mat-select-0")
        centre = next(
            (c for c in centre_options if centre_keyword in c["text"].lower()), None
        )
        if not centre:
            self._log(f"No centre matching '{centre_keyword}' found.")
            return {"result": "error", "reason": "centre_not_found"}

        self._select_mat_option("mat-select-0", centre["id"])
        self.sb.sleep(2)

        appt_cats = self._read_mat_options("mat-select-2")
        if not appt_cats:
            self._log("No appointment category options found.")
            return {"result": "error", "reason": "no_appointment_category", "centre": centre}
        appt_cat = appt_cats[0]
        self._select_mat_option("mat-select-2", appt_cat["id"])
        self.sb.sleep(2)

        sub_cats = self._read_mat_options("mat-select-1")
        if not sub_cats:
            self._log("No sub-category options found.")
            return {
                "result": "error",
                "reason": "sub_category_not_found",
                "centre": centre,
                "appt_cat": appt_cat,
            }

        tourism_matches = [s for s in sub_cats if s["text"].lower() in sub_cat_keywords]
        sub_cats_to_check = tourism_matches or sub_cats
        self._log(
            f"Checking sub-categor{'y' if len(sub_cats_to_check) == 1 else 'ies'}: "
            f"{[s['text'] for s in sub_cats_to_check]}"
        )

        combos = []
        for i, sub_cat in enumerate(sub_cats_to_check):
            if i > 0:
                # A submit can knock Angular off the form or reset its
                # dropdowns, same as check_all_combinations() sees — re-pick
                # centre and appt-category from scratch rather than assume
                # they survived the previous submission.
                self._ensure_on_form()
                self._select_mat_option("mat-select-0", centre["id"])
                self.sb.sleep(3)
                self._select_mat_option("mat-select-2", appt_cat["id"])
                self.sb.sleep(3)

            self._select_mat_option("mat-select-1", sub_cat["id"])
            self.sb.sleep(2)

            result, slot_details = self._submit_and_read_result()
            self._log(f"check_slot_only result ({sub_cat['text']}): {result} — {slot_details}")
            combos.append({"sub_cat": sub_cat, "result": result, "slot_details": slot_details})

            if i < len(sub_cats_to_check) - 1:
                self.sb.sleep(2)

        return {"centre": centre, "appt_cat": appt_cat, "combos": combos}

    def call_check_slot(self, login_user: str, jwt_token: str):
        cfg = self._config
        country_code = cfg["countryCode"]
        mission_code = cfg["missionCode"]
        vac_code = cfg["vacCode"]
        visa_cat = cfg["visaCategoryCode"]
        route = f"{country_code}/en/{mission_code}"

        js_code = f"""
        const done = arguments[0];
        const url = 'https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable';
        const headers = {{
          'accept': 'application/json, text/plain, */*',
          'content-type': 'application/json;charset=UTF-8',
          'authorize': {json.dumps(jwt_token)},
          'route': {json.dumps(route)}
        }};
        const body = {{
          countryCode: {json.dumps(country_code)},
          missionCode: {json.dumps(mission_code)},
          vacCode: {json.dumps(vac_code)},
          visaCategoryCode: {json.dumps(visa_cat)},
          roleName: 'Individual',
          loginUser: {json.dumps(login_user)},
          payCode: ''
        }};
        fetch(url, {{
          method: 'POST',
          headers,
          credentials: 'include',
          body: JSON.stringify(body)
        }})
        .then(async r => {{
          const text = await r.text();
          done(JSON.stringify({{
            status: r.status,
            statusText: r.statusText,
            cfRay: r.headers.get('cf-ray'),
            body: text
          }}));
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

    def call_add_applicant(self) -> dict:
        """
        Register the configured applicant (APPLICANT_CONFIG) on this mission's
        appointment waiting list via POST /appointment/applicants with
        isWaitlist=true. Used when no direct slots are found for a mission
        that has 'waitlist_enabled' set in its COUNTRY_CONFIG entry.
        """
        cfg = self._config
        jwt_token = self.get_auth_token()
        route = f"{cfg['countryCode']}/en/{cfg['missionCode']}"

        applicant = dict(APPLICANT_CONFIG.get(self.login_user, {}).get("api", {}))
        applicant["loginUser"] = self.login_user

        body = {
            "countryCode": cfg["countryCode"],
            "missionCode": cfg["missionCode"],
            "centerCode": cfg["vacCode"],
            "loginUser": self.login_user,
            "visaCategoryCode": cfg["visaCategoryCode"],
            "isEdit": False,
            "feeEntryTypeCode": None,
            "feeExemptionTypeCode": None,
            "feeExemptionDetailsCode": None,
            "applicantList": [applicant],
            "languageCode": "en-US",
            "isWaitlist": True,
            "juridictionCode": None,
            "regionCode": None,
        }

        js_code = f"""
        const done = arguments[0];
        const url = 'https://lift-api.vfsglobal.com/appointment/applicants';
        const headers = {{
          'accept': 'application/json, text/plain, */*',
          'content-type': 'application/json;charset=UTF-8',
          'authorize': {json.dumps(jwt_token)},
          'route': {json.dumps(route)}
        }};
        const body = {json.dumps(body)};
        fetch(url, {{
          method: 'POST',
          headers,
          credentials: 'include',
          body: JSON.stringify(body)
        }})
        .then(async r => {{
          const text = await r.text();
          done(JSON.stringify({{
            status: r.status,
            statusText: r.statusText,
            body: text
          }}));
        }})
        .catch(e => done(JSON.stringify({{ error: e && e.message ? e.message : String(e) }})));
        """

        raw = self.sb.execute_async_script(js_code)
        result = json.loads(raw) if isinstance(raw, str) else raw
        try:
            result["body"] = json.loads(result["body"])
        except (ValueError, TypeError, KeyError):
            pass
        return result
