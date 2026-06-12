from flask import Flask, render_template, request, send_file, Response
import json
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from invoice_generator import build_ubl, build_cii, _merge

app = Flask(__name__)
TEMPLATE_PATH = Path(__file__).parent / "invoice_template.json"


@app.route("/")
def index():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = json.load(f)
    return render_template("index.html", template=template)


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    fmt  = data.pop("_format", "ubl")
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        base = json.load(f)
    merged = _merge(base, data)
    try:
        xml_str = build_ubl(merged) if fmt == "ubl" else build_cii(merged)
    except Exception as e:
        return Response(f"Erreur de génération : {e}", status=400, content_type="text/plain; charset=utf-8")
    inv_id   = merged["invoice"]["id"].replace(" ", "_").replace("/", "-")
    filename = f"{inv_id}_{fmt}.xml"
    buf = io.BytesIO(xml_str.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/xml")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"Serveur demarre : http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
