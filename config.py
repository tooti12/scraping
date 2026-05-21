# config.py

COUNTRY_CONFIG = {
    "nld": {
        "countryCode": "gbr",
        "missionCode": "nld",
        "vacCode": "NAKN",
        "visaCategoryCode": "TA",
    },
    "prt": {
        "countryCode": "gbr",
        "missionCode": "prt",
        "vacCode": "PRT-LON",
        "visaCategoryCode": "TV",
    },
    "mlt": {
        "countryCode": "gbr",
        "missionCode": "mlt",
        "vacCode": "MLT-LON",
        "visaCategoryCode": "TA",
    },
    "bgr": {
        "countryCode": "gbr",
        "missionCode": "bgr",
        "vacCode": "BGR-LON",
        "visaCategoryCode": "TV",
    },
}

TWILIO_CONFIG = {
    "account_sid": "AC0ce99e0bd3abd7748fa67cd01607c54e",
    "auth_token": "83983a62faebdfc2cabb089cbf81d6da",
    "from_number": "+447700101592",
    "to_number": "+447724267222",
}

# Legacy IMAP config for thesemantics.co accounts
EMAIL_CONFIG = {
    "password": "J]r0]a+C.t*g",
    "imap_server": "thesemantics.co",
}

# Gmail config — uses an App Password (not account password)
GMAIL_CONFIG = {
    "imap_server": "imap.gmail.com",
    "app_password": "dbfrklagzbdgnsju",  # App password without spaces
}

# Proxy for VFS requests (residential UK proxy)
PROXY_CONFIG = {
    "proxy": "user-v12T4mAL03J9HGfz-type-residential-session-av7gkchx-country-gb-rotation-0:Kv72bsFi3VQh7f0N@geo.g-w.info:10080",
}
