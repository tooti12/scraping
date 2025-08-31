# self_client.py
from selenium.common.exceptions import TimeoutException
from seleniumbase import SB
import json

class BrowserClient:
    def __init__(self, country, proxy=True, uc=True, headless=False):
        soax_proxy = "package-282130-country-gb-sessionid-mzamPAt0wivcgPkn-sessionlength-150-opt-wb:M5QKXRF1mQoB7edV@proxy.soax.com:5000"
        self.country = country
        self.sb = None
        browser_params = {
            "uc": uc,
            "headless2": headless,
            "incognito": True,
            "proxy": soax_proxy if proxy else None,
        }
        self._sb_ctx = SB(**browser_params)

    def __enter__(self):
        self.sb = self._sb_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sb_ctx.__exit__(exc_type, exc_val, exc_tb)

    def open_login_page(self):
        self.sb.activate_cdp_mode(
            f"https://visa.vfsglobal.com/gbr/en/{self.country}/login"
        )

    def handle_cookies(self):
        try:
            self.sb.wait_for_element("#onetrust-accept-btn-handler", timeout=100)
            self.sb.driver.uc_click("#onetrust-accept-btn-handler")
        except TimeoutException:
            print("No cookie alert appeared within the timeout.")
            return

    def solve_captcha(self):
        try:
            self.sb.uc_gui_click_captcha()
            self.sb.uc_click('button:contains("Submit")')
        except:
            print("Could not solve catpcha")
            pass

     
    def check_is_ip_blocked(self):
        try:
            # Check for informational alert indicating IP block
            alert_selector = 'div[role="alert"].alert-info'
            self.sb.wait_for_element(alert_selector, timeout=10)
            print("IP block alert appeared.")
            return True
        except Exception:
            print("No IP block alert appeared within the timeout.")

        try:
            # Fallback check for error message in page heading
            self.sb.assert_text("Sorry, we’ve been unable to progress", "h1",timeout=4)
            print("IP block detected via heading text.")
            return True
        except Exception as e:
            print("No IP block detected via heading text:", e)
            return False

    def get_auth_token(self):
        return self.sb.execute_script(
            """
        return sessionStorage.getItem('JWT');
        """
        )

    def switch_tabs(self):
        self.sb.click("#mat-select-0",scroll=True)
        self.sb.sleep(2)
        self.sb.cdp.gui_click_element("#NAKN")
        self.sb.sleep(5)
        self.sb.click("#mat-select-0",scroll=True)
        self.sb.sleep(5)
        self.sb.cdp.gui_click_element("#NAKH")
        self.sb.sleep(3)
        self.sb.click("#mat-select-1", scroll=True)
        self.sb.sleep(3)
        self.sb.cdp.gui_click_element("#TA")
        self.solve_captcha()
    def call_check_slot(self, login_user: str, jwt_token: str):
        js_code = f"""
        const done = arguments[0];

        const url = 'https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable';
        const headers = {{
          'accept': 'application/json, text/plain, */*',
          'content-type': 'application/json;charset=UTF-8',
          'authorize': {json.dumps(jwt_token)},
          'route': 'gbr/en/nld'
        }};
        const body = {{
          countryCode: 'gbr',
          missionCode: 'nld',
          vacCode: 'NAKN',
          visaCategoryCode: 'TA',
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
            pass  # leave it as-is if it's not JSON
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
        applicantList: [
            {{
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
            }}
        ],
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
            pass  # leave it as-is if it's not JSON
        return result


    