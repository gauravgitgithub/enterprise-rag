import pytest

from enterprise_rag.ingestion.chunking import Section
from enterprise_rag.ingestion.redaction import PiiRedactor, redact_sections


@pytest.fixture(scope="module")
def redactor() -> PiiRedactor:
    return PiiRedactor()  # loads spaCy once per module


# Break caught: person names reaching embeddings and the LLM.
def test_person_name_is_replaced(redactor):
    assert redactor.redact("Escalate to Priya Sharma today.").text == (
        "Escalate to <PERSON> today."
    )


# Break caught: contact details reaching embeddings and the LLM.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mail priya@acme.com now.", "Mail <EMAIL_ADDRESS> now."),
        ("Call +91 98765 43210 now.", "Call <PHONE_NUMBER> now."),
        ("Card 4111 1111 1111 1111 on file.", "Card <CREDIT_CARD> on file."),
    ],
)
def test_contact_and_payment_identifiers_are_replaced(redactor, text, expected):
    assert redactor.redact(text).text == expected


# Break caught: redacting every entity type, which erases the places and dates that
# retrieval needs ("refund policy for Mumbai stores").
def test_places_and_dates_are_kept(redactor):
    text = "Refunds for Mumbai stores changed on 1 March 2026."
    assert redactor.redact(text).text == text


# Break caught: no audit trail of what was removed from a document.
def test_reports_how_many_of_each_entity_were_removed(redactor):
    result = redactor.redact("Priya Sharma (priya@acme.com) and Rahul Mehta.")
    assert result.entity_counts == {"PERSON": 2, "EMAIL_ADDRESS": 1}


# Break caught: section offsets left pointing into the original text, so citations built from
# chunk spans show the wrong characters once names change length.
def test_redacted_sections_get_offsets_into_the_redacted_document(redactor):
    sections = [
        Section("Intro", "Escalate to Priya Sharma.", page=1, offset=0),
        Section("Contact", "Mail priya@acme.com now.", page=2, offset=27),
    ]

    doc = redact_sections(sections, redactor)

    assert doc.text == "Escalate to <PERSON>.\n\nMail <EMAIL_ADDRESS> now."
    assert [(s.heading_path, s.text, s.page, s.offset) for s in doc.sections] == [
        ("Intro", "Escalate to <PERSON>.", 1, 0),
        ("Contact", "Mail <EMAIL_ADDRESS> now.", 2, 23),
    ]
    assert doc.entity_counts == {"PERSON": 1, "EMAIL_ADDRESS": 1}
