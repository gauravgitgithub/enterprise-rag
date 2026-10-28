# md, not sm: sm misses names without sentence context (e.g. headings). lg adds 380 MB
# for no measured gain on our probes.
SPACY_MODEL = "en_core_web_md"
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
