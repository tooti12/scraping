# browser_client.py
import json
import os

from seleniumbase import SB
from seleniumbase.core import proxy_helper as _sb_proxy_helper

from config import COUNTRY_CONFIG, PROXY_CONFIG


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

        print("[BrowserClient] ── Initialising BrowserClient ──")
        print(f"[BrowserClient]   country   : {country}")
        print("[BrowserClient]   UC mode   : True  (undetected Chrome, bypasses Cloudflare)")
        print("[BrowserClient]   incognito : True  (forced via chromium_arg)")
        print(f"[BrowserClient]   headless  : {headless}")
        print("[BrowserClient]   proxy     : disabled (temporarily commented out)")

        # --incognito: SB's browser_launcher.py deliberately skips this flag when
        # proxy_auth=True or extension_dir is set, because extensions are normally
        # blocked in incognito.  We force it here via chromium_arg, combined with
        # --allow-extensions-in-incognito so the proxy-auth extension still runs.
        chromium_args = [
            "--disable-software-rasterizer",
            "--disable-dev-shm-usage",
            "--incognito",
            "--allow-extensions-in-incognito",
        ]

        browser_params = {
            "uc": True,
            "headless2": headless,
            "chromium_arg": ",".join(chromium_args),
        }

        # --- PROXY TEMPORARILY DISABLED ---
        # Re-enable by removing the `if False` wrapper below.
        # if proxy:
        #     proxy_str = PROXY_CONFIG["proxy"]
        #     host_port = proxy_str.rsplit("@", 1)[-1]
        #     print(f"[BrowserClient]   proxy host: {host_port}")
        #     # Pass proxy= to SB so it runs through its full pipeline:
        #     #   1. Parses user:pass@host:port
        #     #   2. Calls our patched create_proxy_ext → writes MV2 extension
        #     #      (sets chrome.proxy.settings AND handles onAuthRequired)
        #     #   3. Adds --load-extension=<ext_dir> to chrome_options
        #     #   4. Adds --proxy-server=<host:port> to chrome_options
        #     browser_params["proxy"] = proxy_str
        # else:
        #     print("[BrowserClient] No proxy — connecting directly.")
        print("[BrowserClient] Proxy disabled — connecting directly.")

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
        print("[BrowserClient] Browser closed.")

    def open_login_page(self):
        # Visit the country home page first to establish the VFS session cookie.
        # Navigating directly to /login on some missions (e.g. DNK) triggers an
        # Angular "Session Expired" guard because no session cookie exists yet.
        home_url = f"https://visa.vfsglobal.com/gbr/en/{self.country}/"
        print(f"[BrowserClient] Warming up session via home page: {home_url}")
        self.sb.open(home_url)
        self.sb.sleep(3)

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
        print("[BrowserClient] Login page loaded.")

    def handle_cookies(self):
        try:
            self.sb.wait_for_element("#onetrust-accept-btn-handler", timeout=15)
            self.sb.driver.uc_click("#onetrust-accept-btn-handler")
        except Exception:
            # Banner may already be dismissed or may not appear — safe to continue.
            print("[BrowserClient] No cookie banner found, continuing.")

    def solve_captcha(self):
        try:
            self.sb.uc_gui_click_captcha()
            self.sb.uc_click('button:contains("Submit")')
        except Exception:
            print("[BrowserClient] Could not solve captcha (may not be present).")

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
        self._log(f"  Page title: {self.sb.get_title()}")
        self._log(f"  Current URL: {current_url}")

        # Session expired — VFS redirects the dashboard URL to /login.
        # Raise immediately so the caller can handle re-authentication rather than
        # accidentally clicking the login submit button (which is also button.mat-btn-lg).
        if "/login" in current_url:
            raise RuntimeError(
                "SESSION_EXPIRED: dashboard redirected to /login — "
                "VFS session has expired, re-authentication is required."
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

        self.sb.sleep(3)
        self._log("Form ready (#application-detail).")

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

    def _submit_and_read_result(self) -> str:
        """
        Handle the captcha modal, click Submit, and return the outcome string.
        Returns: "slots_available" | "no_slots" | "error"
        """
        try:
            self.sb.wait_for_element("app-cloudflare-dialog", timeout=10)
            self._log("  Captcha modal appeared — solving...")
            self.sb.uc_gui_click_captcha()
            self.sb.sleep(3)
            self._log("  Captcha solved — clicking Submit...")
            self.sb.driver.uc_click("app-cloudflare-dialog button.mat-btn-lg")
            self.sb.sleep(3)
        except Exception:
            self._log("  No captcha modal (auto-passed or not required).")

        try:
            self.sb.wait_for_element(".Information .alert", timeout=8)
            return "no_slots"
        except Exception:
            pass

        try:
            self.sb.wait_for_element("button.mat-btn-lg:not([disabled])", timeout=8)
            return "slots_available"
        except Exception:
            pass

        return "error"

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

        if not centre_options:
            self._log("ERROR: no centre options found — aborting.")
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
                    if sub_cat["text"].lower() == "tourist":
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

                result = self._submit_and_read_result()

            except Exception as e:
                self._log(f"  EXCEPTION: {e}")
                result = "error"

            label = ("*** SLOTS AVAILABLE ***" if result == "slots_available"
                     else ("no slots" if result == "no_slots" else "ERROR"))
            self._log(f"  RESULT [{idx}/{total}]: {label}")
            results.append({
                "combo":    idx,
                "total":    total,
                "centre":   centre,
                "appt_cat": appt_cat,
                "sub_cat":  sub_cat,
                "result":   result,
            })

            if idx < total:
                self.sb.sleep(delay_secs)

        slots_found = [r for r in results if r["result"] == "slots_available"]
        self._log("=" * 55)
        self._log(f"SCAN COMPLETE — {len(slots_found)}/{total} combo(s) with slots available")
        if slots_found:
            for r in slots_found:
                self._log(f"  *** AVAILABLE: {r['centre']['text']} / "
                          f"{r['appt_cat']['text']} / {r['sub_cat']['text']}")
        self._log("=" * 55)
        return results

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

    def call_add_applicant(self, login_user: str, jwt_token: str):
        js_code = f"""
        const done = arguments[0];
        const url = 'https://lift-api.vfsglobal.com/appointment/applicants';
        const headers = {{
          'accept': 'application/json, text/plain, */*',
          'content-type': 'application/json;charset=UTF-8',
          'authorize': {json.dumps(jwt_token)},
          'route': 'gbr/en/nld'
        }};
        const body = {{
          countryCode: 'gbr',
          missionCode: 'nld',
          centerCode: 'NAKH',
          loginUser: {json.dumps(login_user)},
          visaCategoryCode: 'TA',
          isEdit: false,
          feeEntryTypeCode: null,
          feeExemptionTypeCode: null,
          feeExemptionDetailsCode: null,
          applicantList: [{{
            urn: '',
            arn: '',
            loginUser: {json.dumps(login_user)},
            firstName: 'AHMAR',
            employerFirstName: '',
            middleName: '',
            lastName: 'ALI',
            employerLastName: '',
            salutation: '',
            gender: 1,
            nationalId: null,
            VisaToken: null,
            employerContactNumber: '',
            contactNumber: '07724267222',
            dialCode: '44',
            employerDialCode: '',
            passportNumber: 'AX7653221',
            confirmPassportNumber: null,
            passportExpirtyDate: '05/08/2033',
            dateOfBirth: '27/08/2025',
            emailId: 'UMAR@GMAIL.COM',
            employerEmailId: '',
            nationalityCode: 'PAK',
            state: 'HERTS',
            city: 'WGC',
            isEndorsedChild: false,
            applicantType: 0,
            addressline1: '31',
            addressline2: '31',
            pincode: null,
            referenceNumber: null,
            vlnNumber: null,
            applicantGroupId: 0,
            parentPassportNumber: '',
            parentPassportExpiry: '',
            dateOfDeparture: null,
            entryType: '',
            eoiVisaType: '',
            passportType: '',
            vfsReferenceNumber: '',
            familyReunificationCerificateNumber: '',
            PVRequestRefNumber: '',
            PVStatus: '',
            PVStatusDescription: '',
            PVCanAllowRetry: true,
            PVisVerified: false,
            eefRegistrationNumber: '',
            isAutoRefresh: true,
            helloVerifyNumber: '',
            OfflineCClink: '',
            idenfystatuscheck: false,
            vafStatus: null,
            SpecialAssistance: '',
            AdditionalRefNo: null,
            juridictionCode: '',
            canInitiateVAF: false,
            canEditVAF: false,
            canDeleteVAF: false,
            canDownloadVAF: false,
            Retryleft: '',
            ipAddress: '83.106.89.122'
          }}],
          languageCode: 'en-US',
          isWaitlist: true,
          juridictionCode: null,
          regionCode: null
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
