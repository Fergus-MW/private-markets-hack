"""Run inside the ingestion container against the local test database."""
import io
import os
import httpx
from app.store import Store

base = "http://127.0.0.1:8080"
text = b"Tim Cook is the chief executive of Apple. Microsoft hired Satya Nadella."
with httpx.Client(timeout=180) as client:
    assert client.get(base + "/readyz").status_code == 200
    first = client.post(base + "/documents", files={"file": ("sample.txt", text)})
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["people"] >= 1 and result["companies"] >= 1, result
    second = client.post(base + "/documents", files={"file": ("sample.txt", text)})
    assert second.json() == result, second.text
    assert client.post(base + "/documents", files={"file": ("bad.exe", text)}).status_code == 415
    assert client.post(base + "/documents", files={"file": ("empty.txt", b"")}).status_code == 422
    assert client.post(base + "/documents", files={"file": ("broken.pdf", b"not a PDF")}).status_code == 422
    assert client.post(base + "/documents", files={"file": ("huge.txt", b"x" * (20 * 1024 * 1024 + 1))}).status_code == 413
    from docx import Document
    doc = Document()
    doc.add_paragraph(text.decode())
    buffer = io.BytesIO()
    doc.save(buffer)
    response = client.post(base + "/documents", files={"file": ("sample.docx", buffer.getvalue())})
    assert response.status_code == 200, response.text

    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1600, 400), "white")
    ImageDraw.Draw(image).text((50, 100), text.decode(), fill="black", font=ImageFont.load_default(size=30))
    scanned = io.BytesIO()
    image.save(scanned, format="PDF", resolution=150)
    response = client.post(base + "/documents", files={"file": ("scanned.pdf", scanned.getvalue())})
    assert response.status_code == 200, response.text
    assert response.json()["elements"] > 0, response.text

rows = Store().query("SELECT * FROM mention WHERE document = type::thing('document', $key);", {"key": result["document_id"]})[0]["result"]
assert len(rows) == result["mentions"], (len(rows), result)
assert all(row["entity"] and row["text"] for row in rows)
# Verify real transaction rollback, including when HTTP itself returns 200.
try:
    Store().query("BEGIN TRANSACTION; CREATE rollback_probe:test; THROW 'intentional failure'; COMMIT TRANSACTION;")
except RuntimeError:
    pass
else:
    raise AssertionError("Statement failure was swallowed")
assert Store().query("SELECT * FROM rollback_probe:test;")[0]["result"] == []
# Match the cloud database-scoped user authentication path.
Store().query("DEFINE USER OVERWRITE ingestion ON DATABASE PASSWORD 'localtestonly' ROLES EDITOR;")
os.environ.update(SURREAL_AUTH_LEVEL="database", SURREAL_USER="ingestion", SURREAL_PASSWORD="localtestonly")
assert Store().query("RETURN true;")[0]["result"] is True
Store().save({"key": "scoped-test", "elements": []}, [], [])
print("PASS: TXT/DOCX/scanned PDF uploads, entities, provenance, duplicate ingestion, upload validation, rollback, database-scoped writes")
