"""Local Unstructured partitioning; no remote parsing or model API needed."""
import zipfile
import subprocess
from pathlib import Path

SUPPORTED = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx", ".xls", ".xlsx",
    ".csv", ".tsv", ".eml", ".msg", ".txt", ".html", ".htm", ".md", ".rst",
    ".xml", ".epub", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic",
}
MAX_TEXT = 1_000_000


def parse_document(path, pdf_strategy="auto"):
    from unstructured.partition.auto import partition

    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        with open(path, "rb") as source:
            if b"%PDF-" not in source.read(1024):
                raise ValueError("Invalid PDF header")
    if suffix in {".docx", ".pptx", ".xlsx", ".odt", ".epub"}:
        if not zipfile.is_zipfile(path):
            raise ValueError("Invalid Office/EPUB container")
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Expanded document exceeds 100 MiB")
    # Never recursively ingest attachments; submit each as its own source document.
    kwargs = {"filename": str(path), "languages": ["eng"], "include_metadata": True}
    if suffix == ".pdf":
        kwargs["strategy"] = "fast" if pdf_strategy == "auto" else pdf_strategy
        if pdf_strategy == "hi_res":
            kwargs["infer_table_structure"] = True
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic"}:
        kwargs["strategy"] = "ocr_only"
    if suffix in {".eml", ".msg"}:
        kwargs.update(process_attachments=False, include_headers=True)
    if suffix == ".odt":
        # Debian's Pandoc cannot access DOCX reference data in Unstructured's
        # sandboxed ODT conversion. Convert with LibreOffice, then partition DOCX.
        output = Path(path).with_suffix(".docx")
        subprocess.run(["libreoffice", f"-env:UserInstallation={Path(path).parent.as_uri()}/office-profile",
                        "--headless", "--convert-to", "docx", "--outdir", str(Path(path).parent), str(path)],
                       check=True, capture_output=True, timeout=90)
        if not output.exists():
            raise ValueError("ODT conversion failed")
        kwargs["filename"] = str(output)
    parsed = partition(**kwargs)
    warnings = []
    if suffix == ".pdf" and pdf_strategy == "auto":
        if not any(str(e.text).strip() for e in parsed):
            kwargs["strategy"] = "ocr_only"
            parsed = partition(**kwargs)
        else:
            warnings.append("Embedded PDF text used; choose pdf_strategy=ocr_only for mixed scanned/text pages, or hi_res for table layout.")
    if suffix in {".eml", ".msg"}:
        warnings.append("Email attachments are not ingested; upload each attachment separately.")
    if suffix in {".xls", ".xlsx"}:
        warnings.append("Spreadsheet values are extracted as stored; formulas are not recalculated.")
    parsed = [e for e in parsed if str(e.text).strip()]
    if not parsed:
        raise ValueError("Document contains no extractable text")
    if sum(len(str(e.text)) for e in parsed) > MAX_TEXT:
        raise OverflowError("Maximum extracted text is one million characters")
    return parsed, warnings
