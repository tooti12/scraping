# api_client.py
import seleniumbase


class APIClient:
    def __init__(self, auth_token, config, email, browser_client: seleniumbase):
        self.auth_token = auth_token
        self.email = email
        self.browser_client = browser_client
        self.config = config

    def check_slot_availability(self):
        script = self._create_fetch_script()
        return self.browser_client.sb.execute_async_script(script)

    def _create_fetch_script(self):
        js_script = """
                const callback = arguments[arguments.length - 1];

                const body = {{
                    countryCode: "gbr",
                    missionCode: "{missionCode}",
                    vacCode: "{vacCode}",
                    visaCategoryCode: "{visaCategoryCode}",
                    roleName: "Individual",
                    loginUser: "{loginUser}",
                    payCode: ""
                }};

                fetch("https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json;charset=UTF-8",
                        "route": "gbr/en/nld",
                        "authorize": "{authorizeToken}"
                    }},
                    body: JSON.stringify(body),
                    credentials: "include"
                }})
                .then(response => response.json())
                .then(data => callback(data))
                .catch(error => callback({{ error: error.toString() }}));
            """.format(
            missionCode=self.config["missionCode"],
            vacCode=self.config["vacCode"],
            visaCategoryCode=self.config["visaCategoryCode"],
            loginUser=self.email,
            authorizeToken=self.auth_token,
        )

        return js_script
