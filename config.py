# config.py

COUNTRY_CONFIG = {
    "bgr": {
        "countryCode": "gbr",
        "missionCode": "bgr",
        "vacCode": "BGR-LON",       # verify on first run
        "visaCategoryCode": "TA",
        "route": "gbr/en/bgr",
    },
    "nld": {
        "countryCode": "gbr",
        "missionCode": "nld",
        "vacCode": "NAKN",
        "visaCategoryCode": "TA",
        "route": "gbr/en/nld",
    },
    "prt": {
        "countryCode": "gbr",
        "missionCode": "prt",
        "vacCode": "PRT-LON",
        "visaCategoryCode": "TV",
        "route": "gbr/en/prt",
    },
    "mlt": {
        "countryCode": "gbr",
        "missionCode": "mlt",
        "vacCode": "MLT-LON",
        "visaCategoryCode": "TA",
        "route": "gbr/en/mlt",
    },
}

# Residential GB proxy (rotation-0 = sticky session)
PROXY = "user-v12T4mAL03J9HGfz-type-residential-session-av7gkchx-country-gb-rotation-0:Kv72bsFi3VQh7f0N@geo.g-w.info:10080"

# Gmail IMAP — used to fetch VFS OTP emails
GMAIL_CONFIG = {
    "email": "umar.jwork@gmail.com",
    "app_password": "dbfr klag zbdg nsju",
    "imap_server": "imap.gmail.com",
    "imap_port": 993,
    "otp_sender": "donotreply@vfshelpline.com",
}

TWILIO_CONFIG = {
    "account_sid": "AC0ce99e0bd3abd7748fa67cd01607c54e",
    "auth_token": "83983a62faebdfc2cabb089cbf81d6da",
    "from_number": "+447700101592",
    "to_number": "+447724267222",
}