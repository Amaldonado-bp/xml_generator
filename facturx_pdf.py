#!/usr/bin/env python3
"""
Génération de factures Factur-X 1.09 au format PDF/A-3b
avec XML CII D22B embarqué et métadonnées XMP conformes AFNOR XP Z12-014.

Dépendances : reportlab, pypdf
"""

import io
import struct
import zlib
from datetime import datetime

# ── Correspondance profil → ConformanceLevel XMP ─────────────────────
_PROFILE_LABELS = {
    "MINIMUM":  "MINIMUM",
    "BASICWL":  "BASIC WL",
    "BASIC":    "BASIC",
    "EN16931":  "EN 16931",
    "EXTENDED": "EXTENDED",
}

# ── XMP template Factur-X 1.09 ────────────────────────────────────────

def _build_xmp(profile: str, title: str, creator: str, date_str: str) -> bytes:
    """Génère le bloc XMP PDF/A-3b + extension Factur-X."""
    conformance_level = _PROFILE_LABELS.get(profile.upper(), "EN 16931")
    xmp = f"""<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">

    <rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" rdf:about="">
      <pdfaid:part>3</pdfaid:part>
      <pdfaid:conformance>B</pdfaid:conformance>
    </rdf:Description>

    <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" rdf:about="">
      <dc:title>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">{_xml_escape(title)}</rdf:li>
        </rdf:Alt>
      </dc:title>
      <dc:creator>
        <rdf:Seq>
          <rdf:li>{_xml_escape(creator)}</rdf:li>
        </rdf:Seq>
      </dc:creator>
      <dc:description>
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Facture électronique Factur-X {conformance_level}</rdf:li>
        </rdf:Alt>
      </dc:description>
    </rdf:Description>

    <rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" rdf:about="">
      <xmp:CreatorTool>Invoice XML Generator — BearingPoint</xmp:CreatorTool>
      <xmp:CreateDate>{date_str}</xmp:CreateDate>
      <xmp:ModifyDate>{date_str}</xmp:ModifyDate>
    </rdf:Description>

    <rdf:Description xmlns:pdf="http://ns.adobe.com/pdf/1.3/" rdf:about="">
      <pdf:Producer>Invoice XML Generator — BearingPoint</pdf:Producer>
    </rdf:Description>

    <rdf:Description xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
                     xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
                     xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#"
                     rdf:about="">
      <pdfaExtension:schemas>
        <rdf:Bag>
          <rdf:li rdf:parseType="Resource">
            <pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
            <pdfaSchema:namespaceURI>urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#</pdfaSchema:namespaceURI>
            <pdfaSchema:prefix>fx</pdfaSchema:prefix>
            <pdfaSchema:property>
              <rdf:Seq>
                <rdf:li rdf:parseType="Resource">
                  <pdfaProperty:name>DocumentFileName</pdfaProperty:name>
                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>
                  <pdfaProperty:category>external</pdfaProperty:category>
                  <pdfaProperty:description>The name of the embedded XML invoice file</pdfaProperty:description>
                </rdf:li>
                <rdf:li rdf:parseType="Resource">
                  <pdfaProperty:name>DocumentType</pdfaProperty:name>
                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>
                  <pdfaProperty:category>external</pdfaProperty:category>
                  <pdfaProperty:description>The type of the hybrid document</pdfaProperty:description>
                </rdf:li>
                <rdf:li rdf:parseType="Resource">
                  <pdfaProperty:name>Version</pdfaProperty:name>
                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>
                  <pdfaProperty:category>external</pdfaProperty:category>
                  <pdfaProperty:description>The version of the Factur-X specification</pdfaProperty:description>
                </rdf:li>
                <rdf:li rdf:parseType="Resource">
                  <pdfaProperty:name>ConformanceLevel</pdfaProperty:name>
                  <pdfaProperty:valueType>Text</pdfaProperty:valueType>
                  <pdfaProperty:category>external</pdfaProperty:category>
                  <pdfaProperty:description>The Factur-X conformance level</pdfaProperty:description>
                </rdf:li>
              </rdf:Seq>
            </pdfaSchema:property>
          </rdf:li>
        </rdf:Bag>
      </pdfaExtension:schemas>
    </rdf:Description>

    <rdf:Description xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#" rdf:about="">
      <fx:DocumentType>INVOICE</fx:DocumentType>
      <fx:DocumentFileName>factur-x.xml</fx:DocumentFileName>
      <fx:Version>1.0</fx:Version>
      <fx:ConformanceLevel>{conformance_level}</fx:ConformanceLevel>
    </rdf:Description>

  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xmp.encode("utf-8")


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── Génération PDF/A-3b ───────────────────────────────────────────────

def build_facturx_pdf(xml_str: str, invoice_data: dict, profile: str = "EN16931") -> bytes:
    """
    Génère un PDF/A-3b Factur-X avec le XML CII D22B embarqué.

    Paramètres :
      xml_str      : contenu XML de la facture (CII D22B)
      invoice_data : données de la facture (dict avec 'invoice', 'supplier', etc.)
      profile      : profil Factur-X (MINIMUM, BASICWL, BASIC, EN16931, EXTENDED)

    Retourne les bytes du fichier PDF.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
        import pypdf
    except ImportError as e:
        raise ImportError(
            f"Dépendances manquantes pour la génération PDF/A-3 : {e}. "
            "Installez-les avec : pip install reportlab pypdf"
        ) from e

    inv      = invoice_data.get("invoice", {})
    supplier = invoice_data.get("supplier", {})
    buyer    = invoice_data.get("buyer", {})
    lines    = invoice_data.get("lines", [])
    totals   = invoice_data.get("totals", {})
    vat      = invoice_data.get("vat_breakdown", [])

    inv_id    = str(inv.get("id", "FACTURE"))
    inv_date  = str(inv.get("issue_date", ""))
    currency  = str(inv.get("currency", "EUR"))
    now_str   = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # ── 1. Génération du PDF visuel avec reportlab ────────────────────
    pdf_buf = io.BytesIO()
    styles  = getSampleStyleSheet()

    style_h1 = ParagraphStyle("H1", parent=styles["Heading1"],
                               fontSize=16, textColor=colors.HexColor("#FF3D47"),
                               spaceAfter=6)
    style_h2 = ParagraphStyle("H2", parent=styles["Heading2"],
                               fontSize=11, textColor=colors.HexColor("#333333"),
                               spaceAfter=4)
    style_n  = ParagraphStyle("N",  parent=styles["Normal"],
                               fontSize=9, leading=12)
    style_sm = ParagraphStyle("SM", parent=styles["Normal"],
                               fontSize=8, leading=11, textColor=colors.HexColor("#555555"))

    doc = SimpleDocTemplate(pdf_buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    story = []

    # En-tête
    type_labels = {
        "380": "FACTURE", "381": "AVOIR", "384": "FACTURE CORRECTIVE",
        "386": "FACTURE D'ACOMPTE", "389": "AUTOFACTURATION",
        "326": "FACTURE PARTIELLE", "383": "NOTE DE DÉBIT",
    }
    doc_type = type_labels.get(str(inv.get("type_code", "380")), "FACTURE")
    story.append(Paragraph(f"{doc_type} N° {inv_id}", style_h1))
    story.append(Paragraph(f"Date : {inv_date}  |  Devise : {currency}  |  "
                           f"Profil Factur-X : {_PROFILE_LABELS.get(profile.upper(), profile)}",
                           style_sm))
    story.append(Spacer(1, 0.4*cm))

    # Parties
    sup_addr = supplier.get("address", {})
    buy_addr = buyer.get("address",    {})

    def fmt_addr(party_dict, addr_dict):
        lines_a = [f"<b>{party_dict.get('name', '')}</b>"]
        if addr_dict.get("street"):
            lines_a.append(addr_dict["street"])
        if addr_dict.get("additional_street"):
            lines_a.append(addr_dict["additional_street"])
        city_line = " ".join(filter(None, [
            addr_dict.get("postal_zone", ""),
            addr_dict.get("city", ""),
            addr_dict.get("country_code", ""),
        ]))
        if city_line.strip():
            lines_a.append(city_line)
        if party_dict.get("vat_id"):
            lines_a.append(f"N° TVA : {party_dict['vat_id']}")
        if party_dict.get("company_id"):
            lines_a.append(f"SIREN/SIRET : {party_dict['company_id']}")
        return "<br/>".join(lines_a)

    parties_data = [[
        Paragraph(f"<b>ÉMETTEUR</b><br/>{fmt_addr(supplier, sup_addr)}", style_n),
        Paragraph(f"<b>DESTINATAIRE</b><br/>{fmt_addr(buyer, buy_addr)}", style_n),
    ]]
    parties_tbl = Table(parties_data, colWidths=[8.5*cm, 8.5*cm])
    parties_tbl.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(parties_tbl)
    story.append(Spacer(1, 0.5*cm))

    # Lignes de facture
    if lines:
        story.append(Paragraph("Détail des prestations", style_h2))
        tbl_data = [["#", "Désignation", "Qté", "P.U. HT", "Remise", "Net HT", "TVA"]]
        for i, l in enumerate(lines, 1):
            tbl_data.append([
                str(i),
                l.get("name", ""),
                str(l.get("quantity", "")),
                f"{float(l.get('unit_price', 0)):.2f}",
                f"{float(l.get('discount', 0)):.2f}" if l.get("discount") else "—",
                f"{float(l.get('net_amount', 0)):.2f}",
                f"{l.get('vat_category','S')} {l.get('vat_rate',0)}%",
            ])
        col_w = [0.6*cm, 6.5*cm, 1.2*cm, 2*cm, 1.5*cm, 2*cm, 2.2*cm]
        tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#FF3D47")),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTSIZE",    (0,0), (-1,0), 8),
            ("FONTSIZE",    (0,1), (-1,-1), 8),
            ("GRID",        (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDDD")),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F9F9F9")]),
            ("ALIGN",       (2,0), (-1,-1), "RIGHT"),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
            ("PADDING",     (0,0), (-1,-1), 3),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 0.4*cm))

    # Ventilation TVA
    if vat:
        story.append(Paragraph("Ventilation TVA", style_h2))
        vat_data = [["Catégorie", "Base HT", "Taux", "Montant TVA"]]
        for v in vat:
            vat_data.append([
                f"{v.get('category','S')}",
                f"{float(v.get('taxable_amount', 0)):.2f} {currency}",
                f"{v.get('rate', 0)}%",
                f"{float(v.get('tax_amount', 0)):.2f} {currency}",
            ])
        vat_tbl = Table(vat_data, colWidths=[3*cm, 4*cm, 3*cm, 4*cm])
        vat_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#333333")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTSIZE",   (0,0), (-1,-1), 8),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#DDDDDD")),
            ("ALIGN",      (1,0), (-1,-1), "RIGHT"),
            ("PADDING",    (0,0), (-1,-1), 3),
        ]))
        story.append(vat_tbl)
        story.append(Spacer(1, 0.4*cm))

    # Totaux
    story.append(Paragraph("Récapitulatif", style_h2))
    totals_data = [
        ["Total HT",   f"{float(totals.get('tax_exclusive',0)):.2f} {currency}"],
        ["Total TVA",  f"{float(totals.get('tax_amount',0)):.2f} {currency}"],
        ["Total TTC",  f"{float(totals.get('tax_inclusive',0)):.2f} {currency}"],
    ]
    if totals.get("prepaid", 0):
        totals_data.append(["Acompte versé", f"-{float(totals['prepaid']):.2f} {currency}"])
    totals_data.append(["NET À PAYER", f"{float(totals.get('payable',0)):.2f} {currency}"])

    tot_tbl = Table(totals_data, colWidths=[8*cm, 5*cm])
    tot_tbl.setStyle(TableStyle([
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("ALIGN",       (1,0), (-1,-1), "RIGHT"),
        ("PADDING",     (0,0), (-1,-1), 4),
        ("LINEABOVE",   (0,-1), (-1,-1), 1, colors.HexColor("#FF3D47")),
        ("FONTNAME",    (0,-1), (-1,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",   (0,-1), (-1,-1), colors.HexColor("#FF3D47")),
    ]))
    story.append(tot_tbl)

    # Pied de page informatif
    story.append(Spacer(1, 0.8*cm))
    story.append(Paragraph(
        f"<i>Ce document est une facture électronique Factur-X 1.09 "
        f"(profil {_PROFILE_LABELS.get(profile.upper(), profile)}) "
        f"conforme à la norme AFNOR XP Z12-014. "
        f"Le fichier XML CII D22B est embarqué dans ce PDF/A-3b.</i>",
        style_sm
    ))

    doc.build(story)
    pdf_bytes = pdf_buf.getvalue()

    # ── 2. Ajout du XML CII embarqué + XMP via pypdf ─────────────────
    xml_bytes = xml_str.encode("utf-8")
    xmp_bytes = _build_xmp(
        profile=profile,
        title=f"Facture {inv_id}",
        creator=supplier.get("name", ""),
        date_str=now_str,
    )

    reader   = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    writer   = pypdf.PdfWriter()

    # Copie de toutes les pages
    for page in reader.pages:
        writer.add_page(page)

    # Embedding du XML comme fichier annexe (EmbeddedFile)
    writer.add_attachment("factur-x.xml", xml_bytes)

    # Remplacement des XMP metadata
    writer._info = reader.metadata  # type: ignore[attr-defined]
    try:
        writer.add_metadata({
            "/Title":    f"Facture {inv_id}",
            "/Author":   supplier.get("name", ""),
            "/Producer": "Invoice XML Generator — BearingPoint",
            "/Creator":  "Invoice XML Generator — BearingPoint",
        })
    except Exception:
        pass

    # Injection du stream XMP dans le catalogue
    try:
        xmp_stream = pypdf.generic.DecodedStreamObject()
        xmp_stream.set_data(xmp_bytes)
        xmp_stream.update({
            pypdf.generic.NameObject("/Type"):    pypdf.generic.NameObject("/Metadata"),
            pypdf.generic.NameObject("/Subtype"): pypdf.generic.NameObject("/XML"),
        })
        writer._root_object[pypdf.generic.NameObject("/Metadata")] = (  # type: ignore[attr-defined]
            writer._add_object(xmp_stream)  # type: ignore[attr-defined]
        )
    except Exception:
        pass

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()
