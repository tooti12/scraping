# config.py

# Countries shown as cards on the public slot-checker homepage
# (dashboard/templates/home.html). Each "code" must have a COUNTRY_CONFIG
# entry below. Kept separate from main.py's ACCOUNTS list so the existing
# continuous booking monitor never has to change shape for this.
COUNTRIES = [
    {"code": "dnk", "name": "Denmark"},
    {"code": "bgr", "name": "Bulgaria"},
    {"code": "svn", "name": "Slovenia"},
    {"code": "che", "name": "Switzerland"},
]

# Shared VFS login used by the public slot-only checker (see
# slot_check_service.py) — MVP checks on behalf of every visitor with one
# account, same as main.py's monitor does today. Per-visitor credentials can
# replace this once the product needs real multi-tenant accounts.
SHARED_VFS_ACCOUNT = {
    "email": "umar.jwork@gmail.com",
    "password": "P@ssword123",
}

COUNTRY_CONFIG = {
    "nld": {
        "countryCode": "gbr",
        "missionCode": "nld",
        "vacCode": "NAKN",
        "visaCategoryCode": "TA",
        # If no direct slots are found anywhere for this mission, automatically
        # register the applicant (APPLICANT_CONFIG) on the waiting list via
        # POST /appointment/applicants with isWaitlist=true.
        "waitlist_enabled": True,
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
    "dnk": {
        "countryCode": "gbr",
        "missionCode": "dnk",
        "vacCode": "DNK-LON",
        "visaCategoryCode": "TV",
        "ui": {
            "centerCode": None,
            "appointmentCategoryCode": None,
            "subCategoryCode": None,
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
    "svn": {
        "countryCode": "gbr",
        "missionCode": "svn",
        "vacCode": "SVN-LON",
        "visaCategoryCode": "TV",
        "ui": {
            "centerCode": None,
            "appointmentCategoryCode": None,
            "subCategoryCode": None,
        },
    },
    "che": {
        "countryCode": "gbr",
        "missionCode": "che",
        "vacCode": "CHE-LON",
        "visaCategoryCode": "TV",
        "ui": {
            "centerCode": None,
            "appointmentCategoryCode": None,
            "subCategoryCode": None,
        },
    },
}

# One entry per VFS account email, holding the shape call_add_applicant()
# needs for its waitlist POST body (POST /appointment/applicants,
# isWaitlist=true); `loginUser` is filled in at runtime from the active
# account. The 'your-details' applicant form itself is no longer filled
# from a static config — booking_flow.py fetches the live form's fields
# and asks the dashboard for values on every booking instead.
APPLICANT_CONFIG = {
    "vfs@thesemantics.co": {
        "api": {
            "urn": "",
            "arn": "",
            "firstName": "AHMAR",
            "employerFirstName": "",
            "middleName": "",
            "lastName": "ALI",
            "employerLastName": "",
            "salutation": "",
            "gender": 1,
            "nationalId": None,
            "VisaToken": None,
            "employerContactNumber": "",
            "contactNumber": "07724267222",
            "dialCode": "44",
            "employerDialCode": "",
            "passportNumber": "EK1812233",
            "confirmPassportNumber": None,
            "passportExpirtyDate": "03/09/2031",
            "dateOfBirth": "09/08/1995",
            "emailId": "UMARJAVED56@GMAIL.COM",
            "employerEmailId": "",
            "nationalityCode": "PAK",
            "state": "HERTS",
            "city": "WGC",
            "isEndorsedChild": False,
            "applicantType": 0,
            "addressline1": "31",
            "addressline2": "MERRIFIELD",
            "pincode": None,
            "referenceNumber": None,
            "vlnNumber": None,
            "applicantGroupId": 0,
            "parentPassportNumber": "",
            "parentPassportExpiry": "",
            "dateOfDeparture": None,
            "entryType": "",
            "eoiVisaType": "",
            "passportType": "",
            "vfsReferenceNumber": "",
            "familyReunificationCerificateNumber": "",
            "PVRequestRefNumber": "",
            "PVStatus": "",
            "PVStatusDescription": "",
            "PVCanAllowRetry": True,
            "PVisVerified": False,
            "eefRegistrationNumber": "",
            "isAutoRefresh": True,
            "helloVerifyNumber": "",
            "OfflineCClink": "",
            "idenfystatuscheck": False,
            "vafStatus": None,
            "SpecialAssistance": "",
            "AdditionalRefNo": None,
            "juridictionCode": "",
            "canInitiateVAF": False,
            "canEditVAF": False,
            "canDeleteVAF": False,
            "canDownloadVAF": False,
            "Retryleft": "",
            "ipAddress": "83.106.89.122",
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
