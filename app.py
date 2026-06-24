from flask import Flask, render_template, request, send_file, Response
import json
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from invoice_generator import build_ubl, build_cii, _merge, validate_data
from invoice_validator import InvoiceValidator
try:
    from facturx_pdf import build_facturx_pdf
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

app = Flask(__name__)
TEMPLATE_PATH = Path(__file__).parent / "invoice_template.json"


@app.route("/")
def index():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = json.load(f)
    return render_template("index.html", template=template)


@app.route("/generate", methods=["POST"])
def generate():
    data    = request.get_json()
    fmt     = data.pop("_format",  "ubl")
    profile = data.pop("_profile", "EN16931").upper()
    peppol  = bool(data.pop("_peppol", False))
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        base = json.load(f)
    merged = _merge(base, data)
    errors = validate_data(merged, profile=profile)
    if errors:
        return Response(
            json.dumps({"errors": errors}, ensure_ascii=False),
            status=422,
            content_type="application/json; charset=utf-8"
        )
    try:
        if fmt == "ubl":
            xml_str = build_ubl(merged, profile=profile, peppol=peppol)
        else:
            xml_str = build_cii(merged, profile=profile)
    except Exception as e:
        return Response(f"Erreur de génération : {e}", status=400,
                        content_type="text/plain; charset=utf-8")
    inv_id   = merged["invoice"]["id"].replace(" ", "_").replace("/", "-")
    filename = f"{inv_id}_{fmt}_{profile}.xml"
    buf = io.BytesIO(xml_str.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/xml")


@app.route("/generate-pdf", methods=["POST"])
def generate_pdf():
    if not HAS_PDF:
        return Response(
            json.dumps({"error": "Génération PDF/A-3 non disponible. "
                                 "Installez : pip install reportlab pypdf"},
                       ensure_ascii=False),
            status=503,
            content_type="application/json; charset=utf-8"
        )
    data    = request.get_json()
    profile = data.pop("_profile", "EN16931").upper()
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        base = json.load(f)
    merged = _merge(base, data)
    errors = validate_data(merged, profile=profile)
    if errors:
        return Response(
            json.dumps({"errors": errors}, ensure_ascii=False),
            status=422,
            content_type="application/json; charset=utf-8"
        )
    try:
        xml_str  = build_cii(merged, profile=profile)
        pdf_data = build_facturx_pdf(xml_str, merged, profile=profile)
    except Exception as e:
        return Response(f"Erreur de génération PDF : {e}", status=400,
                        content_type="text/plain; charset=utf-8")
    inv_id   = merged["invoice"]["id"].replace(" ", "_").replace("/", "-")
    filename = f"{inv_id}_facturx_{profile}.pdf"
    buf = io.BytesIO(pdf_data)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/pdf")


@app.route("/validate", methods=["POST"])
def validate_xml():
    if "file" not in request.files:
        return Response(
            json.dumps({"error": "Aucun fichier fourni"}, ensure_ascii=False),
            status=400, content_type="application/json; charset=utf-8"
        )
    file = request.files["file"]
    if not file.filename:
        return Response(
            json.dumps({"error": "Nom de fichier manquant"}, ensure_ascii=False),
            status=400, content_type="application/json; charset=utf-8"
        )

    # Sauvegarde temporaire pour le validateur
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml")
    try:
        os.close(tmp_fd)
        file.save(tmp_path)
        validator = InvoiceValidator(tmp_path)
        issues    = validator.validate()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    errors   = [{"code": i.code, "message": i.message, "location": i.location}
                for i in issues if i.severity == "ERROR"]
    warnings = [{"code": i.code, "message": i.message, "location": i.location}
                for i in issues if i.severity == "WARNING"]

    result = {
        "filename": file.filename,
        "format":   validator.fmt,
        "valid":    len(errors) == 0,
        "errors":   errors,
        "warnings": warnings,
    }
    return Response(
        json.dumps(result, ensure_ascii=False),
        status=200,
        content_type="application/json; charset=utf-8"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Serveur démarré : http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
