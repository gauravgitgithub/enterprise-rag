SPACY_MODEL = "en_core_web_sm"
LANGUAGE = "en"

# Only identifiers that point at a person. Places, organisations and dates stay: retrieval
# needs them, and they are rarely personal on their own.
REDACTED_ENTITIES = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
)
