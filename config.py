# config.py

COUNTRY_CONFIG = {
    "nld": {
        "countryCode": "gbr",
        "missionCode": "nld",
        "vacCode": "NAKN",
        "visaCategoryCode": "TA",
        # UI form selections on /application-detail
        # centerCode    → mat-select-0 option id
        # appointmentCategoryCode → mat-select-2 option id (None = pick first available)
        # subCategoryCode → mat-select-1 option id
        "ui": {
            "centerCode": "NAKN",
            "appointmentCategoryCode": None,
            "subCategoryCode": "TA",
        },
    },
    "prt": {
        "countryCode": "gbr",
        "missionCode": "prt",
        "vacCode": "PRT-LON",
        "visaCategoryCode": "TV",
        "ui": {
            "centerCode": "PRT-LON",
            "appointmentCategoryCode": None,
            "subCategoryCode": "TV",
        },
    },
    "mlt": {
        "countryCode": "gbr",
        "missionCode": "mlt",
        "vacCode": "MLT-LON",
        "visaCategoryCode": "TA",
        "ui": {
            "centerCode": "MLT-LON",
            "appointmentCategoryCode": None,
            "subCategoryCode": "TA",
        },
    },
    "bgr": {
        "countryCode": "gbr",
        "missionCode": "bgr",
        "vacCode": "BGR-LON",
        "visaCategoryCode": "TV",
        "ui": {
            # Option element IDs from inspect-element on /application-detail:
            # BLUKED = Edinburgh, BLUKLN = London, BLULMN = Manchester
            "centerCode": "BLUKLN",
            # Appointment category (mat-select-2) options load after centre is chosen.
            # Set to None to auto-select the first option that appears.
            "appointmentCategoryCode": None,
            # Sub-category (mat-select-1): BUS, BUVFF, EU, VFF, TOU
            "subCategoryCode": "TOU",
        },
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
