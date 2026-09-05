"""Stable, source-grounded context records built from Unstructured elements."""
import hashlib

PIPELINE_VERSION = "unstructured-0.18.15_context-v2"
CHUNK_SIZE = 4000


def stable_id(*parts):
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


def source_metadata(element):
    metadata = element.metadata.to_dict()
    # Do not leak temporary filenames, paths, or internal parser IDs.
    allowed = {"page_number", "page_name", "filetype", "languages", "text_as_html",
               "link_urls", "link_texts", "links", "sent_from", "sent_to", "subject",
               "email_message_id", "category_depth", "coordinates"}
    return {key: value for key, value in metadata.items() if key in allowed and value is not None}


def prepare_context(parsed, document_id, filename, chunker):
    elements = []
    section_title = None
    for index, element in enumerate(parsed):
        element.text = str(element.text)
        element.metadata.source_element_index = index
        if element.category == "Title":
            section_title = element.text
        elements.append({"element_id": stable_id(document_id, "element", index, element.text),
                         "index": index, "type": element.category, "text": element.text,
                         "section_title": section_title,
                         "metadata": source_metadata(element)})
    chunks = []
    for index, chunk in enumerate(chunker(parsed, max_characters=CHUNK_SIZE,
            new_after_n_chars=3000, combine_text_under_n_chars=0,
            multipage_sections=False, overlap=200, include_orig_elements=True)):
        sources = []
        for original in chunk.metadata.orig_elements or []:
            element_index = original.metadata.source_element_index
            element = elements[element_index]
            source = {"element_id": element["element_id"], "element_index": element_index,
                      "type": element["type"], "section_title": element["section_title"]}
            for field in ("page_number", "page_name"):
                if field in element["metadata"]:
                    source[field] = element["metadata"][field]
            if source not in sources:
                sources.append(source)
        key = stable_id(document_id, PIPELINE_VERSION, "chunk", index, str(chunk.text))
        chunks.append({"chunk_id": key, "index": index, "type": chunk.category,
                       "text": str(chunk.text), "sources": sources,
                       "citation": {"document_id": document_id, "filename": filename,
                                    "chunk_id": key}})
    return elements, chunks


def context_page(document, offset=0, limit=10, max_characters=20000):
    """Fit whole chunks into a bounded text budget, always making forward progress."""
    chunks = document["chunks"]
    selected, used = [], 0
    for chunk in chunks[offset:offset + limit]:
        if used + len(chunk["text"]) > max_characters:
            break
        selected.append(chunk)
        used += len(chunk["text"])
    end = offset + len(selected)
    return {"document_id": document["key"], "filename": document["filename"],
            "pipeline_version": document["pipeline_version"], "chunks": selected,
            "total_chunks": len(chunks), "characters": used,
            "next_offset": end if end < len(chunks) else None,
            "warnings": document.get("warnings", []),
            "content_role": "source_material"}
