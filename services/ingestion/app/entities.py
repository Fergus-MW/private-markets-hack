"""Deterministic candidate IDs; name equality is not proof of identity."""
import hashlib
import unicodedata

PIPELINE_VERSION = "unstructured-0.18.15_spacy-en-3.8.0_canonical-v1"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def extract(elements, nlp, document_id):
    entities, mentions = {}, []
    for index, (element, parsed) in enumerate(zip(elements, nlp.pipe(e["text"] for e in elements))):
        for span in parsed.ents:
            if span.label_ not in {"PERSON", "ORG"}:
                continue
            kind = "person" if span.label_ == "PERSON" else "company"
            name = normalize(span.text)
            if not name:
                continue
            # Names alone cannot safely identify a person across documents.
            scope = document_id if kind == "person" else "name_candidate"
            key = digest(f"{kind}:{scope}:{name}")
            entity = entities.setdefault(key, {
                "key": key, "kind": kind, "canonical_name": name,
                "aliases": [], "resolution_status": "unverified_name_candidate",
                "scope": scope,
            })
            if span.text not in entity["aliases"]:
                entity["aliases"].append(span.text)
            mentions.append({
                "key": digest(f"{document_id}:{PIPELINE_VERSION}:{index}:{span.start_char}:{span.end_char}:{key}"),
                "entity_key": key, "kind": kind, "text": span.text,
                "element_index": index, "page_number": element.get("page_number"),
                "start": span.start_char, "end": span.end_char,
                "model_label": span.label_,
            })
    return list(entities.values()), mentions
