from flask import Flask, render_template, request, send_file, Response
import json
import io
import os
import sys
import tempfile
import zipfile as zipmod
import csv
from decimal import Decimal, ROUND_HALF_UP
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


def _validate_xml_bytes(xml_bytes: bytes, filename: str) -> dict:
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml")
    try:
        os.close(tmp_fd)
        with open(tmp_path, "wb") as f:
            f.write(xml_bytes)
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
    return {
        "filename": filename,
        "format":   validator.fmt,
        "profile":  validator.profile,
        "valid":    len(errors) == 0,
        "errors":   errors,
        "warnings": warnings,
    }


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
    result = _validate_xml_bytes(file.read(), file.filename)
    return Response(
        json.dumps(result, ensure_ascii=False),
        status=200,
        content_type="application/json; charset=utf-8"
    )


@app.route("/validate-batch", methods=["POST"])
def validate_batch():
    files = request.files.getlist("files[]")
    if not files:
        return Response(
            json.dumps({"error": "Aucun fichier fourni"}, ensure_ascii=False),
            status=400, content_type="application/json; charset=utf-8"
        )
    results = []
    for file in files:
        fname = file.filename or "fichier.xml"
        if fname.lower().endswith(".zip"):
            zip_bytes = file.read()
            try:
                with zipmod.ZipFile(io.BytesIO(zip_bytes)) as zf:
                    for name in sorted(zf.namelist()):
                        if name.lower().endswith(".xml") and not name.startswith("__MACOSX"):
                            xml_bytes = zf.read(name)
                            basename  = name.rsplit("/", 1)[-1]
                            results.append(_validate_xml_bytes(xml_bytes, basename))
            except zipmod.BadZipFile:
                results.append({
                    "filename": fname, "format": None, "profile": None, "valid": False,
                    "errors":   [{"code": "ZIP-ERR",
                                  "message": "Fichier ZIP invalide ou corrompu",
                                  "location": ""}],
                    "warnings": [],
                })
        else:
            results.append(_validate_xml_bytes(file.read(), fname))
    return Response(
        json.dumps({"results": results}, ensure_ascii=False),
        status=200,
        content_type="application/json; charset=utf-8"
    )


_CSV_HEADERS = [
    "format*", "profile*", "invoice_id*", "invoice_date*", "due_date",
    "type_code*", "currency*", "buyer_reference*", "purchase_order_ref",
    "sales_order_ref", "contract_ref", "payment_terms", "payment_means", "iban",
    "preceding_ref", "preceding_date", "note",
    "supplier_name*", "supplier_vat*", "supplier_company_id",
    "supplier_street", "supplier_city", "supplier_postal", "supplier_country",
    "buyer_name*", "buyer_vat", "buyer_company_id",
    "buyer_street", "buyer_city", "buyer_postal", "buyer_country", "buyer_endpoint",
    "line_id*", "line_name*", "line_description",
    "line_qty*", "line_unit", "line_unit_price*", "line_base_qty",
    "line_discount", "line_vat_category*", "line_vat_rate*",
]

_CSV_EXAMPLES = [
    # INV-001 ligne 1
    ["cii", "EN16931", "INV-001", "2026-06-26", "2026-07-26",
     "380", "EUR", "REF-ACHETEUR-001", "PO-12345", "", "", "30 jours nets", "30", "FR7617789000011234567800012",
     "", "", "",
     "Mon Entreprise SAS", "FR12345678901", "123456789",
     "1 rue de la Paix", "PARIS", "75001", "FR",
     "Client SAS", "FR98765432109", "987654321",
     "10 avenue des Clients", "LYON", "69001", "FR", "",
     "1", "Prestation de conseil", "Conseil en stratégie digitale", "5", "HUR", "150.00", "", "0", "S", "20"],
    # INV-001 ligne 2
    ["cii", "EN16931", "INV-001", "2026-06-26", "2026-07-26",
     "380", "EUR", "REF-ACHETEUR-001", "PO-12345", "", "", "30 jours nets", "30", "FR7617789000011234567800012",
     "", "", "",
     "Mon Entreprise SAS", "FR12345678901", "123456789",
     "1 rue de la Paix", "PARIS", "75001", "FR",
     "Client SAS", "FR98765432109", "987654321",
     "10 avenue des Clients", "LYON", "69001", "FR", "",
     "2", "Fourniture materiel", "", "10", "EA", "25.00", "", "0", "S", "20"],
    # INV-002 (UBL, facture simple 1 ligne, avec qte base)
    ["ubl", "EN16931", "INV-002", "2026-06-26", "",
     "380", "EUR", "REF-CLIENT-2", "PO-67890", "", "", "45 jours nets", "30", "",
     "", "", "",
     "Mon Entreprise SAS", "FR12345678901", "123456789",
     "1 rue de la Paix", "PARIS", "75001", "FR",
     "Autre Client SARL", "FR11223344556", "112233445",
     "5 bd de la Republique", "MARSEILLE", "13001", "FR", "",
     "1", "Licence logicielle", "Abonnement annuel", "1", "EA", "1200.00", "", "0", "S", "20"],
    # INV-003 (avoir CII - necessite preceding_ref et preceding_date)
    ["cii", "EN16931", "AVOIR-001", "2026-06-26", "",
     "381", "EUR", "REF-ACHETEUR-001", "PO-12345", "", "", "30 jours nets", "30", "",
     "INV-001", "2026-06-01", "Avoir total sur INV-001",
     "Mon Entreprise SAS", "FR12345678901", "123456789",
     "1 rue de la Paix", "PARIS", "75001", "FR",
     "Client SAS", "FR98765432109", "987654321",
     "10 avenue des Clients", "LYON", "69001", "FR", "",
     "1", "Annulation prestation conseil", "", "-5", "HUR", "150.00", "", "0", "S", "20"],
]


def _compute_totals(lines, charges=None):
    groups = {}
    line_ext = Decimal("0")
    for line in lines:
        qty      = Decimal(str(line.get("quantity", 1)))
        price    = Decimal(str(line.get("unit_price", 0)))
        base_qty = Decimal(str(line.get("base_quantity") or 1))
        discount = Decimal(str(line.get("discount", 0)))
        net = (qty * price / base_qty - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        line["net_amount"] = float(net)
        line_ext += net
        cat  = line.get("vat_category", "S")
        rate = Decimal(str(line.get("vat_rate", 20)))
        key  = f"{cat}|{rate}"
        if key not in groups:
            groups[key] = {"category": cat, "rate": float(rate), "taxable": Decimal("0")}
        groups[key]["taxable"] += net

    charge_total = allowance_total = Decimal("0")
    for ch in (charges or []):
        amt = Decimal(str(ch.get("amount", 0)))
        if ch.get("is_charge", True):
            charge_total += amt
        else:
            allowance_total += amt

    tax_exclusive = line_ext - allowance_total + charge_total
    tax_amount    = Decimal("0")
    vat_breakdown = []
    for g in groups.values():
        taxable = g["taxable"].quantize(Decimal("0.01"))
        tax     = (taxable * Decimal(str(g["rate"])) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_amount += tax
        vat_breakdown.append({"category": g["category"], "rate": g["rate"],
                               "taxable_amount": float(taxable), "tax_amount": float(tax)})

    tax_inclusive = tax_exclusive + tax_amount
    q = lambda v: float(v.quantize(Decimal("0.01")))
    return {
        "totals": {
            "line_extension":  q(line_ext),
            "allowance_total": q(allowance_total),
            "charge_total":    q(charge_total),
            "tax_exclusive":   q(tax_exclusive),
            "tax_inclusive":   q(tax_inclusive),
            "tax_amount":      q(tax_amount),
            "payable":         q(tax_inclusive),
            "prepaid":         0,
        },
        "vat_breakdown": vat_breakdown,
    }


def _detect_delimiter(text: str) -> str:
    first_line = text.split("\n")[0]
    return ";" if first_line.count(";") >= first_line.count(",") else ","


@app.route("/template-csv")
def template_csv():
    buf = io.StringIO()
    w   = csv.writer(buf, delimiter=";")
    w.writerow(_CSV_HEADERS)
    w.writerows(_CSV_EXAMPLES)
    buf.seek(0)
    content = "﻿" + buf.getvalue()   # BOM UTF-8 pour Excel FR
    return Response(
        content,
        status=200,
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=template_factures.csv"},
    )


@app.route("/generate-batch-csv", methods=["POST"])
def generate_batch_csv():
    file = request.files.get("csv_file")
    if not file:
        return Response(json.dumps({"error": "Aucun fichier CSV fourni"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    raw = file.read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return Response(json.dumps({"error": "Encodage du fichier CSV non reconnu"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    delim = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    # Normalise les en-têtes : supprime le '*' des colonnes obligatoires
    all_rows = [{k.strip().rstrip("*"): v for k, v in row.items()} for row in reader]

    # Regroupe les lignes par invoice_id (conserve l'ordre d'apparition)
    invoices = {}
    order    = []
    for row in all_rows:
        inv_id = (row.get("invoice_id") or "").strip()
        if not inv_id:
            continue
        if inv_id not in invoices:
            invoices[inv_id] = {"meta": dict(row), "lines": []}
            order.append(inv_id)
        invoices[inv_id]["lines"].append(row)

    if not invoices:
        return Response(json.dumps({"error": "Aucune facture trouvée dans le CSV (colonne invoice_id manquante ou vide)"},
                                   ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    successes = []
    errors    = []
    zip_buf   = io.BytesIO()

    with zipmod.ZipFile(zip_buf, "w", zipmod.ZIP_DEFLATED) as zf:
        for inv_id in order:
            meta  = invoices[inv_id]["meta"]
            rows  = invoices[inv_id]["lines"]
            fmt   = (meta.get("format") or "cii").strip().lower()
            profile = (meta.get("profile") or "EN16931").strip().upper()

            def s(key, default=""):
                return (meta.get(key) or default).strip()

            lines = []
            for i, r in enumerate(rows, start=1):
                base_qty_raw = (r.get("line_base_qty") or "").strip()
                lines.append({
                    "id":           int((r.get("line_id") or str(i)).strip() or i),
                    "name":         (r.get("line_name") or "").strip(),
                    "description":  (r.get("line_description") or "").strip(),
                    "quantity":     float((r.get("line_qty") or "1").strip() or 1),
                    "unit_code":    (r.get("line_unit") or "EA").strip(),
                    "unit_price":   float((r.get("line_unit_price") or "0").strip() or 0),
                    "base_quantity": float(base_qty_raw) if base_qty_raw else None,
                    "discount":     float((r.get("line_discount") or "0").strip() or 0),
                    "vat_category": (r.get("line_vat_category") or "S").strip(),
                    "vat_rate":     float((r.get("line_vat_rate") or "20").strip() or 20),
                })

            computed = _compute_totals(lines)
            d = {
                "_format":  fmt,
                "_profile": profile,
                "invoice": {
                    "id":                   inv_id,
                    "issue_date":           s("invoice_date"),
                    "due_date":             s("due_date"),
                    "type_code":            s("type_code", "380"),
                    "currency":             s("currency", "EUR"),
                    "buyer_reference":      s("buyer_reference"),
                    "purchase_order_ref":   s("purchase_order_ref"),
                    "sales_order_ref":      s("sales_order_ref"),
                    "contract_ref":         s("contract_ref"),
                    "note":                 s("note"),
                    "payment_terms_note":   s("payment_terms"),
                    "preceding_invoice_ref":  s("preceding_ref"),
                    "preceding_invoice_date": s("preceding_date"),
                },
                "supplier": {
                    "name":            s("supplier_name"),
                    "vat_id":          s("supplier_vat"),
                    "company_id":      s("supplier_company_id"),
                    "endpoint_id":     "",
                    "endpoint_scheme": "0088",
                    "address": {
                        "street":       s("supplier_street"),
                        "city":         s("supplier_city"),
                        "postal_zone":  s("supplier_postal"),
                        "country_code": s("supplier_country", "FR"),
                    },
                    "contact": {"name": "", "phone": "", "email": ""},
                },
                "buyer": {
                    "name":            s("buyer_name"),
                    "vat_id":          s("buyer_vat"),
                    "company_id":      s("buyer_company_id"),
                    "endpoint_id":     s("buyer_endpoint"),
                    "endpoint_scheme": "0088",
                    "address": {
                        "street":       s("buyer_street"),
                        "city":         s("buyer_city"),
                        "postal_zone":  s("buyer_postal"),
                        "country_code": s("buyer_country", "FR"),
                    },
                    "contact": {"name": "", "phone": "", "email": ""},
                },
                "payment": {
                    "means_code":   s("payment_means", "30"),
                    "iban":         s("iban"),
                    "bic":          "",
                    "account_name": "",
                },
                "lines":   lines,
                "charges": [],
                "totals":         computed["totals"],
                "vat_breakdown":  computed["vat_breakdown"],
            }

            try:
                val_errors = validate_data(d, profile=profile)
                if val_errors:
                    errors.append({"invoice_id": inv_id, "errors": val_errors})
                    continue
                if fmt == "ubl":
                    xml_str = build_ubl(d, profile=profile)
                    ext = "ubl"
                else:
                    xml_str = build_cii(d, profile=profile)
                    ext = "cii"
                filename = f"{inv_id.replace('/', '-').replace(' ', '_')}_{ext}_EN16931.xml"
                zf.writestr(filename, xml_str.encode("utf-8"))
                successes.append(inv_id)
            except Exception as e:
                errors.append({"invoice_id": inv_id, "errors": [str(e)]})

        # Inclut un rapport dans le ZIP si erreurs
        if errors:
            report = json.dumps({"success": successes, "errors": errors},
                                ensure_ascii=False, indent=2)
            zf.writestr("_rapport_erreurs.json", report.encode("utf-8"))

    if not successes:
        err_detail = "; ".join(f"{e['invoice_id']}: {e['errors']}" for e in errors)
        return Response(
            json.dumps({"error": f"Aucune facture générée. Erreurs : {err_detail}"},
                       ensure_ascii=False),
            status=422, content_type="application/json; charset=utf-8"
        )

    zip_buf.seek(0)
    summary_header = json.dumps({"ok": len(successes), "errors": len(errors)})
    return Response(
        zip_buf.read(),
        status=200,
        content_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=factures_xml.zip",
            "X-Batch-Summary":     summary_header,
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Serveur démarré : http://localhost:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)
