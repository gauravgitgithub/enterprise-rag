from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

from enterprise_rag.constants.documents import SECTION_SEPARATOR
from enterprise_rag.constants.redaction import LANGUAGE, REDACTED_ENTITIES, SPACY_MODEL
from enterprise_rag.ingestion.chunking import Section


@dataclass(frozen=True, slots=True)
class Redacted:
    text: str
    entity_counts: dict[str, int] = field(default_factory=dict)


class PiiRedactor:
    """Replaces personal identifiers with <ENTITY_TYPE>. Loads spaCy: build once per process."""

    def __init__(self) -> None:
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}],
            }
        ).create_engine()
        self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=[LANGUAGE])
        self._anonymizer = AnonymizerEngine()

    def redact(self, text: str) -> Redacted:
        findings = self._analyzer.analyze(
            text=text, language=LANGUAGE, entities=list(REDACTED_ENTITIES)
        )
        # Presidio's two packages declare separate RecognizerResult types; same shape at runtime.
        result = self._anonymizer.anonymize(text=text, analyzer_results=findings)  # type: ignore[arg-type]
        # Count what was actually replaced, after overlapping findings were merged.
        return Redacted(result.text, dict(Counter(item.entity_type for item in result.items)))


@dataclass(frozen=True, slots=True)
class RedactedDocument:
    text: str
    sections: list[Section]
    entity_counts: dict[str, int]


def redact_sections(sections: Sequence[Section], redactor: PiiRedactor) -> RedactedDocument:
    """Redact each section and lay them out again, so offsets index the redacted text."""
    out: list[Section] = []
    counts: Counter[str] = Counter()
    offset = 0
    for section in sections:
        redacted = redactor.redact(section.text)
        out.append(replace(section, text=redacted.text, offset=offset))
        counts.update(redacted.entity_counts)
        offset += len(redacted.text) + len(SECTION_SEPARATOR)
    return RedactedDocument(SECTION_SEPARATOR.join(s.text for s in out), out, dict(counts))
