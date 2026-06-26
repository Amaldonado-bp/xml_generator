#!/usr/bin/env python3
"""
Générateur de factures EN16931/Factur-X 1.09 — UBL 2.1 / CII D22B
Profils Factur-X : MINIMUM, BASIC WL, BASIC, EN16931, EXTENDED
Supporte aussi : PEPPOL BIS Billing 3.0 (UBL)
"""

import json
import re
import sys
import argparse
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom

# ── Namespaces UBL ────────────────────────────────────────────────────
_UBL_INV    = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_UBL_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
_UBL_CAC    = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_UBL_CBC    = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

# ── Namespaces CII ────────────────────────────────────────────────────
_CII_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_CII_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_CII_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

TEMPLATE_PATH = Path(__file__).parent / "invoice_template.json"

# Codes type UN/EDIFACT D.22B acceptés par Factur-X 1.09 / EN16931
VALID_TYPE_CODES = {
    "80", "82", "84", "130", "202", "203", "204", "211",
    "261", "262", "295", "296", "308", "325", "326", "380",
    "381", "382", "383", "384", "385", "386", "387", "388",
    "389", "390", "393", "394", "395", "396", "420", "456",
    "457", "458", "527", "575", "623", "633", "751", "780",
    "817", "870", "875", "876", "877", "935",
}
ZERO_VAT_CATS     = {"E", "AE", "K", "G", "O", "Z"}
VALID_VAT_CATS    = {"S", "Z", "E", "AE", "K", "G", "O"}

# Profils Factur-X 1.09 et leurs URNs de spécification (AFNOR XP Z12-014)
FACTURX_PROFILES = {
    "MINIMUM":  "urn:factur-x.eu:1p0:minimum",
    "BASICWL":  "urn:factur-x.eu:1p0:basicwl",
    "BASIC":    "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic",
    "EN16931":  "urn:cen.eu:en16931:2017",
    "EXTENDED": "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended",
}
PEPPOL_BIS_ID      = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
_PROFILES_NO_LINES = {"MINIMUM", "BASICWL"}  # Ces profils n'ont pas de lignes de facture


# ── Validation des données ────────────────────────────────────────────

def validate_data(d: dict, profile: str = "EN16931") -> list:
    """
    Vérifie la conformité EN16931/Factur-X des données avant génération.
    Retourne une liste de chaînes d'erreur. Vide = données valides.
    """
    errors = []
    inv      = d.get("invoice", {})
    supplier = d.get("supplier", {})
    buyer    = d.get("buyer", {})
    lines    = d.get("lines", [])
    totals   = d.get("totals", {})
    vat      = d.get("vat_breakdown", [])

    # ── BT-1 : Numéro de facture ─────────────────────────────────────
    if not str(inv.get("id", "")).strip():
        errors.append("BT-1 : Le numéro de facture est obligatoire")

    # ── BT-2 : Date d'émission ───────────────────────────────────────
    issue_date = str(inv.get("issue_date", "")).strip()
    if not issue_date:
        errors.append("BT-2 : La date d'émission est obligatoire")
    elif not re.match(r"^\d{4}-\d{2}-\d{2}$", issue_date):
        errors.append(f"BT-2 : Format de date invalide '{issue_date}' (attendu AAAA-MM-JJ)")

    # ── BT-3 : Code type de document ─────────────────────────────────
    type_code = str(inv.get("type_code", "380")).strip()
    if type_code not in VALID_TYPE_CODES:
        errors.append(f"BT-3 : Code type invalide '{type_code}' "
                      f"(codes acceptés Factur-X 1.09 : 380=facture, 381=avoir, 384=corrective, "
                      f"389=autofacturation, 326=partielle, 386=acompte, ...)")

    # ── BT-25/26 : Référence facture initiale (obligatoire pour avoirs) ─
    _AVOIR_CODES = {"381", "261", "296"}
    if type_code in _AVOIR_CODES:
        if not inv.get("preceding_invoice_ref", "").strip():
            errors.append("BT-25 : Le numéro de la facture initiale est obligatoire pour un avoir")
        prec_date = inv.get("preceding_invoice_date", "").strip()
        if not prec_date:
            errors.append("BT-26 : La date de la facture initiale est obligatoire pour un avoir")
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", prec_date):
            errors.append(f"BT-26 : Format de date invalide '{prec_date}' (attendu AAAA-MM-JJ)")

    # ── BT-5 : Code monnaie ──────────────────────────────────────────
    currency = str(inv.get("currency", "")).strip()
    if not currency:
        errors.append("BT-5 : Le code monnaie est obligatoire")
    elif not re.match(r"^[A-Z]{3}$", currency):
        errors.append(f"BT-5 : Code monnaie invalide '{currency}' (format ISO 4217, ex : EUR)")

    # ── BT-9 : Date d'échéance (format si présente) ──────────────────
    due_date = str(inv.get("due_date", "")).strip()
    if due_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", due_date):
        errors.append(f"BT-9 : Format de date d'échéance invalide '{due_date}' (attendu AAAA-MM-JJ)")

    # ── BT-10 : Référence acheteur ───────────────────────────────────
    if not str(inv.get("buyer_reference", "")).strip():
        errors.append("BT-10 : La référence acheteur est obligatoire")

    # ── BG-4 : Fournisseur ───────────────────────────────────────────
    if not str(supplier.get("name", "")).strip():
        errors.append("BT-27 : Le nom du fournisseur est obligatoire")

    if not supplier.get("vat_id") and not supplier.get("company_id"):
        errors.append("BT-31/BT-30 : L'identifiant fournisseur (TVA ou SIREN) est obligatoire")

    sup_vat = str(supplier.get("vat_id", "")).strip()
    if sup_vat and not re.match(r"^[A-Z]{2}[0-9A-Z]{2,12}$", sup_vat):
        errors.append(f"BT-31 : Format du numéro TVA fournisseur suspect : '{sup_vat}' (ex : FR07433927332)")

    s_addr = supplier.get("address", {})
    if not str(s_addr.get("street", "")).strip():
        errors.append("BT-35 : La rue du fournisseur est obligatoire")
    if not str(s_addr.get("city", "")).strip():
        errors.append("BT-37 : La ville du fournisseur est obligatoire")
    if not str(s_addr.get("postal_zone", "")).strip():
        errors.append("BT-38 : Le code postal du fournisseur est obligatoire")
    s_country = str(s_addr.get("country_code", "")).strip()
    if not s_country:
        errors.append("BT-40 : Le code pays du fournisseur est obligatoire")
    elif not re.match(r"^[A-Z]{2}$", s_country):
        errors.append(f"BT-40 : Code pays invalide '{s_country}' (format ISO 3166-1 alpha-2, ex : FR)")

    # ── BG-7 : Acheteur ──────────────────────────────────────────────
    if not str(buyer.get("name", "")).strip():
        errors.append("BT-44 : Le nom de l'acheteur est obligatoire")

    b_addr = buyer.get("address", {})
    b_country = str(b_addr.get("country_code", "")).strip()
    if b_country and not re.match(r"^[A-Z]{2}$", b_country):
        errors.append(f"BT-55 : Code pays acheteur invalide '{b_country}'")

    # ── BG-25 : Lignes ───────────────────────────────────────────────
    if not lines and profile.upper() not in _PROFILES_NO_LINES:
        errors.append("BG-25 : Au moins une ligne de facture est obligatoire")

    for i, line in enumerate(lines, 1):
        lbl = f"Ligne {i}"
        if not str(line.get("name", "")).strip():
            errors.append(f"BT-153 ({lbl}) : Le nom de l'article est obligatoire")
        if line.get("quantity") is None:
            errors.append(f"BT-129 ({lbl}) : La quantité est obligatoire")
        if line.get("unit_price") is None:
            errors.append(f"BT-146 ({lbl}) : Le prix unitaire est obligatoire")
        if line.get("net_amount") is None:
            errors.append(f"BT-131 ({lbl}) : Le montant net est obligatoire")
        cat = str(line.get("vat_category", "S")).strip()
        if cat not in VALID_VAT_CATS:
            errors.append(f"BT-151 ({lbl}) : Catégorie TVA invalide '{cat}' (valeurs : {', '.join(sorted(VALID_VAT_CATS))})")
        rate = float(line.get("vat_rate", 0))
        if cat in ZERO_VAT_CATS and rate != 0:
            errors.append(f"BT-152 ({lbl}) : Taux TVA doit être 0% pour la catégorie {cat}")

        # BR-CO-03 : net = qty × (prix / qté_base) - remise
        try:
            qty      = Decimal(str(line["quantity"]))
            uprice   = Decimal(str(line["unit_price"]))
            base_qty = Decimal(str(line["base_quantity"])) if line.get("base_quantity") else Decimal("1")
            discount = Decimal(str(line.get("discount", 0)))
            net      = Decimal(str(line["net_amount"]))
            expected = (qty * uprice / base_qty - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(net - expected) > Decimal("0.02"):
                errors.append(
                    f"BR-CO-03 ({lbl}) : Montant net ({net}) ≠ Qté×(Prix/QtéBase)−Remise ({expected})"
                )
        except Exception:
            pass

    # ── BG-22 : Cohérence des totaux ─────────────────────────────────
    try:
        line_ext   = Decimal(str(totals.get("line_extension", 0)))
        allow      = Decimal(str(totals.get("allowance_total", 0)))
        charges    = Decimal(str(totals.get("charge_total", 0)))
        tax_excl   = Decimal(str(totals.get("tax_exclusive", 0)))
        tax_incl   = Decimal(str(totals.get("tax_inclusive", 0)))
        tax_amt    = Decimal(str(totals.get("tax_amount", 0)))
        prepaid    = Decimal(str(totals.get("prepaid", 0)))
        payable    = Decimal(str(totals.get("payable", 0)))

        # BR-CO-11 : BT-109 = BT-106 − BT-107 + BT-108
        expected_excl = line_ext - allow + charges
        if abs(tax_excl - expected_excl) > Decimal("0.02"):
            errors.append(
                f"BR-CO-11 : Montant HT ({tax_excl}) ≠ LignesHT − Remises + Frais ({expected_excl})"
            )
        # BR-CO-13 : BT-112 = BT-109 + BT-110
        expected_incl = tax_excl + tax_amt
        if abs(tax_incl - expected_incl) > Decimal("0.02"):
            errors.append(
                f"BR-CO-13 : Montant TTC ({tax_incl}) ≠ HT + TVA ({expected_incl})"
            )
        # BR-CO-16 : BT-115 = BT-112 − BT-113
        expected_payable = tax_incl - prepaid
        if abs(payable - expected_payable) > Decimal("0.02"):
            errors.append(
                f"BR-CO-16 : Montant à payer ({payable}) ≠ TTC − Acompte ({expected_payable})"
            )
    except Exception:
        pass

    return errors


# ── Chargement des données ────────────────────────────────────────────

def load_data(config_path: str) -> dict:
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = json.load(f)
    with open(config_path, "r", encoding="utf-8") as f:
        override = json.load(f)
    return _merge(template, override)


def _merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = _merge(result[k], v)
        else:
            result[k] = v
    return result


# ── Générateur UBL 2.1 ────────────────────────────────────────────────

def build_ubl(d: dict, profile: str = "EN16931", peppol: bool = False) -> str:
    type_code = str(d["invoice"].get("type_code", "380"))
    is_credit = type_code == "381"
    doc_ns    = _UBL_CREDIT if is_credit else _UBL_INV
    root_tag  = "CreditNote"        if is_credit else "Invoice"
    line_tag  = "CreditNoteLine"    if is_credit else "InvoiceLine"
    qty_tag   = "CreditedQuantity"  if is_credit else "InvoicedQuantity"
    type_el   = "CreditNoteTypeCode" if is_credit else "InvoiceTypeCode"

    ET.register_namespace("",    doc_ns)
    ET.register_namespace("cac", _UBL_CAC)
    ET.register_namespace("cbc", _UBL_CBC)

    inv      = d["invoice"]
    supplier = d["supplier"]
    buyer    = d["buyer"]
    lines    = d["lines"]
    totals   = d["totals"]
    vat      = d.get("vat_breakdown", [])
    payment  = d.get("payment", {})
    charges  = d.get("charges", [])
    cur      = inv.get("currency", "EUR")

    def cac(t): return f"{{{_UBL_CAC}}}{t}"
    def cbc(t): return f"{{{_UBL_CBC}}}{t}"

    root = ET.Element(f"{{{doc_ns}}}{root_tag}")

    # ── En-tête ──────────────────────────────────────────────────────
    cid_ubl = PEPPOL_BIS_ID if peppol else FACTURX_PROFILES.get(profile.upper(), FACTURX_PROFILES["EN16931"])
    _t(root, cbc("CustomizationID"), cid_ubl)                             # BT-24
    _t(root, cbc("ID"),              inv["id"])                            # BT-1
    _t(root, cbc("IssueDate"),       inv["issue_date"])                    # BT-2
    if inv.get("due_date"):
        _t(root, cbc("DueDate"), inv["due_date"])                          # BT-9
    _t(root, cbc(type_el),           type_code)                            # BT-3
    if inv.get("note"):
        _t(root, cbc("Note"), inv["note"])                                 # BT-22
    _t(root, cbc("DocumentCurrencyCode"), cur)                             # BT-5
    if inv.get("buyer_accounting_ref"):
        _t(root, cbc("AccountingCost"), inv["buyer_accounting_ref"])       # BT-19
    _t(root, cbc("BuyerReference"), inv.get("buyer_reference", ""))        # BT-10

    if inv.get("purchase_order_ref"):
        or_ = ET.SubElement(root, cac("OrderReference"))
        _t(or_, cbc("ID"), inv["purchase_order_ref"])                      # BT-13
        if inv.get("sales_order_ref"):
            _t(or_, cbc("SalesOrderID"), inv["sales_order_ref"])

    if inv.get("contract_ref"):
        cr = ET.SubElement(root, cac("ContractDocumentReference"))
        _t(cr, cbc("ID"), inv["contract_ref"])                             # BT-12

    if inv.get("preceding_invoice_ref"):                                   # BT-25/26
        br  = ET.SubElement(root, cac("BillingReference"))
        idr = ET.SubElement(br, cac("InvoiceDocumentReference"))
        _t(idr, cbc("ID"), inv["preceding_invoice_ref"])
        if inv.get("preceding_invoice_date"):
            _t(idr, cbc("IssueDate"), inv["preceding_invoice_date"])

    # ── BG-4 Fournisseur ─────────────────────────────────────────────
    asp = ET.SubElement(root, cac("AccountingSupplierParty"))
    sp  = ET.SubElement(asp, cac("Party"))
    if supplier.get("endpoint_id"):
        ep = ET.SubElement(sp, cbc("EndpointID"))
        ep.text = supplier["endpoint_id"]
        ep.set("schemeID", supplier.get("endpoint_scheme", "0088"))
    pn = ET.SubElement(sp, cac("PartyName"))
    _t(pn, cbc("Name"), supplier["name"])
    _ubl_address(sp, supplier.get("address", {}), cac, cbc)
    if supplier.get("vat_id"):
        pts = ET.SubElement(sp, cac("PartyTaxScheme"))
        cid = ET.SubElement(pts, cbc("CompanyID"))
        cid.text = supplier["vat_id"]
        cid.set("schemeID", "VAT")
        _t(ET.SubElement(pts, cac("TaxScheme")), cbc("ID"), "VAT")
    ple = ET.SubElement(sp, cac("PartyLegalEntity"))
    _t(ple, cbc("RegistrationName"), supplier["name"])
    _t(ple, cbc("CompanyID"), supplier.get("company_id", ""))
    if any(supplier.get("contact", {}).get(k) for k in ("name", "phone", "email")):
        _ubl_contact(sp, supplier["contact"], cac, cbc)

    # ── BG-7 Acheteur ────────────────────────────────────────────────
    acp = ET.SubElement(root, cac("AccountingCustomerParty"))
    bp  = ET.SubElement(acp, cac("Party"))
    if buyer.get("endpoint_id"):
        ep2 = ET.SubElement(bp, cbc("EndpointID"))
        ep2.text = buyer["endpoint_id"]
        ep2.set("schemeID", buyer.get("endpoint_scheme", "0088"))
    pn2 = ET.SubElement(bp, cac("PartyName"))
    _t(pn2, cbc("Name"), buyer["name"])
    _ubl_address(bp, buyer.get("address", {}), cac, cbc)
    if buyer.get("vat_id"):
        pts2 = ET.SubElement(bp, cac("PartyTaxScheme"))
        cid2 = ET.SubElement(pts2, cbc("CompanyID"))
        cid2.text = buyer["vat_id"]
        cid2.set("schemeID", "VAT")
        _t(ET.SubElement(pts2, cac("TaxScheme")), cbc("ID"), "VAT")
    ple2 = ET.SubElement(bp, cac("PartyLegalEntity"))
    _t(ple2, cbc("RegistrationName"), buyer["name"])
    _t(ple2, cbc("CompanyID"), buyer.get("company_id", ""))
    if buyer.get("contact"):
        _ubl_contact(bp, buyer["contact"], cac, cbc)

    # ── BG-10 Bénéficiaire ───────────────────────────────────────────
    if payment.get("account_name"):                                        # BT-59
        pp  = ET.SubElement(root, cac("PayeeParty"))
        ppn = ET.SubElement(pp, cac("PartyName"))
        _t(ppn, cbc("Name"), payment["account_name"])

    # ── BG-16 Paiement ───────────────────────────────────────────────
    if payment.get("means_code"):
        pm = ET.SubElement(root, cac("PaymentMeans"))
        _t(pm, cbc("PaymentMeansCode"), payment["means_code"])
        if inv.get("due_date"):
            _t(pm, cbc("PaymentDueDate"), inv["due_date"])
        if payment.get("iban"):
            pfa = ET.SubElement(pm, cac("PayeeFinancialAccount"))
            _t(pfa, cbc("ID"), payment["iban"])
            if payment.get("account_name"):
                _t(pfa, cbc("Name"), payment["account_name"])
            if payment.get("bic"):
                fib = ET.SubElement(pfa, cac("FinancialInstitutionBranch"))
                _t(fib, cbc("ID"), payment["bic"])  # BT-86 — BIC directement dans BranchID (UBL 2.1)

    if inv.get("payment_terms_note"):
        pt = ET.SubElement(root, cac("PaymentTerms"))
        _t(pt, cbc("Note"), inv["payment_terms_note"])                     # BT-20

    # ── BG-20/BG-21 Frais & Remises niveau document ──────────────────
    for ch in charges:
        is_ch = ch.get("is_charge", True)
        amt   = float(ch.get("amount", 0))
        ac = ET.SubElement(root, cac("AllowanceCharge"))
        _t(ac, cbc("ChargeIndicator"), "true" if is_ch else "false")
        if ch.get("description"):
            _t(ac, cbc("AllowanceChargeReason"), ch["description"])
        _cur(ET.SubElement(ac, cbc("Amount")), amt, cur)
        tc = ET.SubElement(ac, cac("TaxCategory"))
        _t(tc, cbc("ID"),      ch.get("vat_category", "S"))
        _t(tc, cbc("Percent"), str(ch.get("vat_rate", 20)))
        _t(ET.SubElement(tc, cac("TaxScheme")), cbc("ID"), "VAT")

    # ── BG-23 TVA ────────────────────────────────────────────────────
    tt = ET.SubElement(root, cac("TaxTotal"))
    _cur(ET.SubElement(tt, cbc("TaxAmount")), totals["tax_amount"], cur)
    for v in vat:
        sub = ET.SubElement(tt, cac("TaxSubtotal"))
        _cur(ET.SubElement(sub, cbc("TaxableAmount")), v["taxable_amount"], cur)
        _cur(ET.SubElement(sub, cbc("TaxAmount")),     v["tax_amount"],     cur)
        tc = ET.SubElement(sub, cac("TaxCategory"))
        _t(tc, cbc("ID"),      v.get("category", "S"))
        _t(tc, cbc("Percent"), str(v.get("rate", 20)))
        _t(ET.SubElement(tc, cac("TaxScheme")), cbc("ID"), "VAT")
        if v.get("exemption_reason"):
            _t(tc, cbc("TaxExemptionReason"), v["exemption_reason"])

    # ── BG-22 Totaux document ────────────────────────────────────────
    lmt = ET.SubElement(root, cac("LegalMonetaryTotal"))
    _cur(ET.SubElement(lmt, cbc("LineExtensionAmount")), totals["line_extension"], cur)  # BT-106
    _cur(ET.SubElement(lmt, cbc("TaxExclusiveAmount")),  totals["tax_exclusive"],  cur)  # BT-109
    _cur(ET.SubElement(lmt, cbc("TaxInclusiveAmount")),  totals["tax_inclusive"],  cur)  # BT-112
    if totals.get("allowance_total", 0):
        _cur(ET.SubElement(lmt, cbc("AllowanceTotalAmount")), totals["allowance_total"], cur)  # BT-107
    if totals.get("charge_total", 0):
        _cur(ET.SubElement(lmt, cbc("ChargeTotalAmount")),    totals["charge_total"],    cur)  # BT-108
    if totals.get("prepaid", 0):
        _cur(ET.SubElement(lmt, cbc("PrepaidAmount")), totals["prepaid"], cur)                 # BT-113
    _cur(ET.SubElement(lmt, cbc("PayableAmount")), totals["payable"], cur)                     # BT-115

    # ── BG-25 Lignes ─────────────────────────────────────────────────
    for line in lines:
        il = ET.SubElement(root, cac(line_tag))
        _t(il, cbc("ID"), str(line["id"]))
        if line.get("note"):
            _t(il, cbc("Note"), line["note"])
        iq = ET.SubElement(il, cbc(qty_tag))
        iq.text = str(line["quantity"])
        iq.set("unitCode", line.get("unit_code", "EA"))
        _cur(ET.SubElement(il, cbc("LineExtensionAmount")), line["net_amount"], cur)
        if line.get("order_line_ref"):
            _t(ET.SubElement(il, cac("OrderLineReference")), cbc("LineID"), line["order_line_ref"])
        # BG-27 Remise sur ligne
        if float(line.get("discount", 0)) > 0:
            lac = ET.SubElement(il, cac("AllowanceCharge"))
            _t(lac, cbc("ChargeIndicator"), "false")
            _cur(ET.SubElement(lac, cbc("Amount")), line["discount"], cur)
            ltc = ET.SubElement(lac, cac("TaxCategory"))
            _t(ltc, cbc("ID"),      line.get("vat_category", "S"))
            _t(ltc, cbc("Percent"), str(line.get("vat_rate", 20)))
            _t(ET.SubElement(ltc, cac("TaxScheme")), cbc("ID"), "VAT")
        item = ET.SubElement(il, cac("Item"))
        if line.get("description"):
            _t(item, cbc("Description"), line["description"])
        _t(item, cbc("Name"), line["name"])
        itc = ET.SubElement(item, cac("ClassifiedTaxCategory"))
        _t(itc, cbc("ID"),      line.get("vat_category", "S"))
        _t(itc, cbc("Percent"), str(line.get("vat_rate", 20)))
        _t(ET.SubElement(itc, cac("TaxScheme")), cbc("ID"), "VAT")
        price = ET.SubElement(il, cac("Price"))
        _cur(ET.SubElement(price, cbc("PriceAmount")), line["unit_price"], cur)
        if line.get("base_quantity"):
            bq = ET.SubElement(price, cbc("BaseQuantity"))
            bq.text = str(line["base_quantity"])
            bq.set("unitCode", line.get("unit_code", "EA"))

    return _pretty_xml(root)


def _ubl_address(parent, addr, cac, cbc):
    pa = ET.SubElement(parent, cac("PostalAddress"))
    if addr.get("street"):            _t(pa, cbc("StreetName"),           addr["street"])
    if addr.get("additional_street"): _t(pa, cbc("AdditionalStreetName"), addr["additional_street"])
    if addr.get("city"):              _t(pa, cbc("CityName"),             addr["city"])
    if addr.get("postal_zone"):       _t(pa, cbc("PostalZone"),           addr["postal_zone"])
    country = ET.SubElement(pa, cac("Country"))
    _t(country, cbc("IdentificationCode"), addr.get("country_code", "FR"))


def _ubl_contact(parent, contact, cac, cbc):
    c = ET.SubElement(parent, cac("Contact"))
    if contact.get("name"):  _t(c, cbc("Name"),           contact["name"])
    if contact.get("phone"): _t(c, cbc("Telephone"),      contact["phone"])
    if contact.get("email"): _t(c, cbc("ElectronicMail"), contact["email"])


# ── Générateur CII D22B (Factur-X 1.09) ──────────────────────────────

def build_cii(d: dict, profile: str = "EN16931") -> str:
    ET.register_namespace("rsm", _CII_RSM)
    ET.register_namespace("ram", _CII_RAM)
    ET.register_namespace("udt", _CII_UDT)

    inv      = d["invoice"]
    supplier = d["supplier"]
    buyer    = d["buyer"]
    lines    = d["lines"]
    totals   = d["totals"]
    vat      = d.get("vat_breakdown", [])
    payment  = d.get("payment", {})
    charges  = d.get("charges", [])
    cur      = inv.get("currency", "EUR")

    def rsm(t): return f"{{{_CII_RSM}}}{t}"
    def ram(t): return f"{{{_CII_RAM}}}{t}"
    def udt(t): return f"{{{_CII_UDT}}}{t}"

    root = ET.Element(rsm("CrossIndustryInvoice"))

    # ExchangedDocumentContext
    ctx  = ET.SubElement(root, rsm("ExchangedDocumentContext"))
    gbpi = ET.SubElement(ctx, ram("GuidelineSpecifiedDocumentContextParameter"))
    _t(gbpi, ram("ID"), FACTURX_PROFILES.get(profile.upper(), FACTURX_PROFILES["EN16931"]))

    # ExchangedDocument
    doc = ET.SubElement(root, rsm("ExchangedDocument"))
    _t(doc, ram("ID"),       inv["id"])
    _t(doc, ram("TypeCode"), inv.get("type_code", "380"))
    idt = ET.SubElement(doc, ram("IssueDateTime"))
    dts = ET.SubElement(idt, udt("DateTimeString"))
    dts.text = inv["issue_date"].replace("-", "")
    dts.set("format", "102")
    if inv.get("note"):
        inc = ET.SubElement(doc, ram("IncludedNote"))
        _t(inc, ram("Content"), inv["note"])

    # SupplyChainTradeTransaction
    sctt = ET.SubElement(root, rsm("SupplyChainTradeTransaction"))

    # Lignes — absentes pour les profils MINIMUM et BASIC WL
    for line in ([] if profile.upper() in _PROFILES_NO_LINES else lines):
        li  = ET.SubElement(sctt, ram("IncludedSupplyChainTradeLineItem"))
        lad = ET.SubElement(li, ram("AssociatedDocumentLineDocument"))
        _t(lad, ram("LineID"), str(line["id"]))
        if line.get("note"):
            _t(ET.SubElement(lad, ram("IncludedNote")), ram("Content"), line["note"])
        sptp = ET.SubElement(li, ram("SpecifiedTradeProduct"))
        _t(sptp, ram("Name"), line["name"])
        if line.get("description"):
            _t(sptp, ram("Description"), line["description"])
        la = ET.SubElement(li, ram("SpecifiedLineTradeAgreement"))
        np_ = ET.SubElement(la, ram("NetPriceProductTradePrice"))
        _t(np_, ram("ChargeAmount"), f"{float(line['unit_price']):.2f}")
        if line.get("base_quantity"):
            bq_el = ET.SubElement(np_, ram("BasisQuantity"))
            bq_el.text = str(line["base_quantity"])
            bq_el.set("unitCode", line.get("unit_code", "EA"))
        ld = ET.SubElement(li, ram("SpecifiedLineTradeDelivery"))
        bq = ET.SubElement(ld, ram("BilledQuantity"))
        bq.text = str(line["quantity"])
        bq.set("unitCode", line.get("unit_code", "EA"))
        ls   = ET.SubElement(li, ram("SpecifiedLineTradeSettlement"))
        atax = ET.SubElement(ls, ram("ApplicableTradeTax"))
        _t(atax, ram("TypeCode"),              "VAT")
        _t(atax, ram("CategoryCode"),          line.get("vat_category", "S"))
        _t(atax, ram("RateApplicablePercent"), str(line.get("vat_rate", 20)))
        # Remise sur ligne
        if float(line.get("discount", 0)) > 0:
            lch = ET.SubElement(ls, ram("SpecifiedTradeAllowanceCharge"))
            ind = ET.SubElement(lch, ram("ChargeIndicator"))
            _t(ind, udt("Indicator"), "false")
            _t(lch, ram("ActualAmount"), f"{float(line['discount']):.2f}")
        sms = ET.SubElement(ls, ram("SpecifiedTradeSettlementLineMonetarySummation"))
        _t(sms, ram("LineTotalAmount"), f"{float(line['net_amount']):.2f}")

    # ApplicableHeaderTradeAgreement
    hta = ET.SubElement(sctt, ram("ApplicableHeaderTradeAgreement"))
    if inv.get("buyer_reference"):
        _t(hta, ram("BuyerReference"), inv["buyer_reference"])
    seller_el = ET.SubElement(hta, ram("SellerTradeParty"))
    _t(seller_el, ram("Name"), supplier["name"])
    if supplier.get("company_id"):
        _t(ET.SubElement(seller_el, ram("SpecifiedLegalOrganization")), ram("ID"), supplier["company_id"])
    _cii_address(seller_el, supplier.get("address", {}), ram)
    if supplier.get("vat_id"):
        stax = ET.SubElement(seller_el, ram("SpecifiedTaxRegistration"))
        vid  = ET.SubElement(stax, ram("ID"))
        vid.text = supplier["vat_id"]
        vid.set("schemeID", "VA")
    if any(supplier.get("contact", {}).get(k) for k in ("name", "phone", "email")):
        _cii_contact(seller_el, supplier["contact"], ram)
    buyer_el = ET.SubElement(hta, ram("BuyerTradeParty"))
    _t(buyer_el, ram("Name"), buyer["name"])
    if buyer.get("company_id"):
        _t(ET.SubElement(buyer_el, ram("SpecifiedLegalOrganization")), ram("ID"), buyer["company_id"])
    _cii_address(buyer_el, buyer.get("address", {}), ram)
    if buyer.get("vat_id"):
        btax = ET.SubElement(buyer_el, ram("SpecifiedTaxRegistration"))
        bid  = ET.SubElement(btax, ram("ID"))
        bid.text = buyer["vat_id"]
        bid.set("schemeID", "VA")
    if inv.get("sales_order_ref"):                                         # BT-14 — avant BuyerOrder dans le schéma CII D22B
        _t(ET.SubElement(hta, ram("SellerOrderReferencedDocument")), ram("IssuerAssignedID"), inv["sales_order_ref"])
    if inv.get("purchase_order_ref"):
        _t(ET.SubElement(hta, ram("BuyerOrderReferencedDocument")), ram("IssuerAssignedID"), inv["purchase_order_ref"])
    if inv.get("contract_ref"):
        _t(ET.SubElement(hta, ram("ContractReferencedDocument")), ram("IssuerAssignedID"), inv["contract_ref"])

    if inv.get("preceding_invoice_ref"):                                   # BT-25/26
        ird = ET.SubElement(hta, ram("InvoiceReferencedDocument"))
        _t(ird, ram("IssuerAssignedID"), inv["preceding_invoice_ref"])
        if inv.get("preceding_invoice_date"):
            fidt = ET.SubElement(ird, ram("FormattedIssueDateTime"))
            dts2 = ET.SubElement(fidt, udt("DateTimeString"))
            dts2.text = inv["preceding_invoice_date"].replace("-", "")
            dts2.set("format", "102")

    # ApplicableHeaderTradeDelivery (obligatoire CII)
    ET.SubElement(sctt, ram("ApplicableHeaderTradeDelivery"))

    # ApplicableHeaderTradeSettlement
    hts = ET.SubElement(sctt, ram("ApplicableHeaderTradeSettlement"))
    _t(hts, ram("InvoiceCurrencyCode"), cur)
    if payment.get("means_code"):
        pm = ET.SubElement(hts, ram("SpecifiedTradeSettlementPaymentMeans"))
        _t(pm, ram("TypeCode"), payment["means_code"])
        if payment.get("iban"):
            pfa = ET.SubElement(pm, ram("PayeePartyCreditorFinancialAccount"))
            _t(pfa, ram("IBANID"), payment["iban"])
            if payment.get("account_name"):
                _t(pfa, ram("AccountName"), payment["account_name"])       # BT-85
        if payment.get("bic"):
            fi = ET.SubElement(pm, ram("PayeeSpecifiedCreditorFinancialInstitution"))
            _t(fi, ram("BICID"), payment["bic"])
    for v in vat:
        atax = ET.SubElement(hts, ram("ApplicableTradeTax"))
        _t(atax, ram("CalculatedAmount"),       f"{float(v['tax_amount']):.2f}")
        _t(atax, ram("TypeCode"),               "VAT")
        _t(atax, ram("BasisAmount"),            f"{float(v['taxable_amount']):.2f}")
        _t(atax, ram("CategoryCode"),           v.get("category", "S"))
        _t(atax, ram("RateApplicablePercent"),  str(v.get("rate", 20)))
        if v.get("exemption_reason"):
            _t(atax, ram("ExemptionReason"), v["exemption_reason"])
    # Frais & Remises niveau document
    for ch in charges:
        is_ch = ch.get("is_charge", True)
        amt   = float(ch.get("amount", 0))
        cch = ET.SubElement(hts, ram("SpecifiedTradeAllowanceCharge"))
        ind = ET.SubElement(cch, ram("ChargeIndicator"))
        _t(ind, udt("Indicator"), "true" if is_ch else "false")
        _t(cch, ram("ActualAmount"), f"{amt:.2f}")
        if ch.get("description"):
            _t(cch, ram("Reason"), ch["description"])
        ctax = ET.SubElement(cch, ram("CategoryTradeTax"))
        _t(ctax, ram("TypeCode"),              "VAT")
        _t(ctax, ram("CategoryCode"),          ch.get("vat_category", "S"))
        _t(ctax, ram("RateApplicablePercent"), str(ch.get("vat_rate", 20)))
    if inv.get("payment_terms_note") or inv.get("due_date"):
        spt = ET.SubElement(hts, ram("SpecifiedTradePaymentTerms"))
        if inv.get("payment_terms_note"):
            _t(spt, ram("Description"), inv["payment_terms_note"])
        if inv.get("due_date"):
            ddate = ET.SubElement(spt, ram("DueDateDateTime"))
            dds   = ET.SubElement(ddate, udt("DateTimeString"))
            dds.text = inv["due_date"].replace("-", "")
            dds.set("format", "102")
    sms = ET.SubElement(hts, ram("SpecifiedTradeSettlementHeaderMonetarySummation"))
    _t(sms, ram("LineTotalAmount"),     f"{float(totals['line_extension']):.2f}")   # BT-106
    if totals.get("charge_total", 0):
        _t(sms, ram("ChargeTotalAmount"),    f"{float(totals['charge_total']):.2f}")   # BT-108
    if totals.get("allowance_total", 0):
        _t(sms, ram("AllowanceTotalAmount"), f"{float(totals['allowance_total']):.2f}") # BT-107
    _t(sms, ram("TaxBasisTotalAmount"), f"{float(totals['tax_exclusive']):.2f}")   # BT-109
    # BT-110 — TaxTotalAmount doit porter currencyID (requis EN16931/Factur-X)
    tta = ET.SubElement(sms, ram("TaxTotalAmount"))
    tta.text = f"{float(totals['tax_amount']):.2f}"
    tta.set("currencyID", cur)
    _t(sms, ram("GrandTotalAmount"),    f"{float(totals['tax_inclusive']):.2f}")   # BT-112
    if totals.get("prepaid", 0):
        _t(sms, ram("TotalPrepaidAmount"), f"{float(totals['prepaid']):.2f}")      # BT-113
    _t(sms, ram("DuePayableAmount"), f"{float(totals['payable']):.2f}")            # BT-115

    return _pretty_xml(root)


def _cii_address(parent, addr, ram):
    if not addr:
        return
    pa = ET.SubElement(parent, ram("PostalTradeAddress"))
    if addr.get("postal_zone"):       _t(pa, ram("PostcodeCode"), addr["postal_zone"])
    if addr.get("street"):            _t(pa, ram("LineOne"),      addr["street"])
    if addr.get("additional_street"): _t(pa, ram("LineTwo"),      addr["additional_street"])
    if addr.get("city"):              _t(pa, ram("CityName"),     addr["city"])
    _t(pa, ram("CountryID"), addr.get("country_code", "FR"))


def _cii_contact(parent, contact, ram):
    sc = ET.SubElement(parent, ram("DefinedTradeContact"))
    if contact.get("name"):  _t(sc, ram("PersonName"), contact["name"])
    if contact.get("phone"):
        ph = ET.SubElement(sc, ram("TelephoneUniversalCommunication"))
        _t(ph, ram("CompleteNumber"), contact["phone"])
    if contact.get("email"):
        em = ET.SubElement(sc, ram("EmailURIUniversalCommunication"))
        _t(em, ram("URIID"), contact["email"])


# ── Utilitaires ───────────────────────────────────────────────────────

def _t(parent, tag, text):
    el = ET.SubElement(parent, tag)
    el.text = str(text) if text is not None else ""
    return el


def _cur(el, amount, currency):
    el.text = f"{float(amount):.2f}"
    el.set("currencyID", currency)


def _pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    dom = minidom.parseString(raw.encode("utf-8"))
    lines = dom.toprettyxml(indent="  ").split("\n")
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out += [l for l in lines[1:] if l.strip()]
    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Générateur de factures Factur-X 1.09 / EN16931 (UBL 2.1 / CII D22B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python invoice_generator.py invoice_example.json
  python invoice_generator.py invoice_example.json --format cii --profile EN16931
  python invoice_generator.py invoice_example.json --format cii --profile MINIMUM
  python invoice_generator.py invoice_example.json --format ubl --peppol
  python invoice_generator.py invoice_example.json --format ubl -o ma_facture.xml
        """
    )
    parser.add_argument("config", help="Fichier JSON avec les champs variables")
    parser.add_argument("--format", choices=["ubl", "cii"], default="ubl",
                        help="Format de sortie : ubl (défaut) ou cii")
    parser.add_argument("--profile",
                        choices=["MINIMUM", "BASICWL", "BASIC", "EN16931", "EXTENDED"],
                        default="EN16931",
                        help="Profil Factur-X 1.09 (défaut : EN16931)")
    parser.add_argument("--peppol", action="store_true",
                        help="Utiliser PEPPOL BIS Billing 3.0 comme CustomizationID (UBL uniquement)")
    parser.add_argument("-o", "--output", help="Chemin du fichier XML de sortie")
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"Erreur : fichier introuvable : {args.config}", file=sys.stderr)
        sys.exit(1)

    data   = load_data(args.config)
    errors = validate_data(data, profile=args.profile)
    if errors:
        print("Erreurs de validation :", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "ubl":
        xml_str = build_ubl(data, profile=args.profile, peppol=args.peppol)
    else:
        xml_str = build_cii(data, profile=args.profile)

    inv_id = data["invoice"]["id"].replace(" ", "_").replace("/", "-")
    output = args.output or f"{inv_id}_{args.format}.xml"

    with open(output, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"Facture generee : {output}  ({args.format.upper()} / {args.profile})")


if __name__ == "__main__":
    main()
