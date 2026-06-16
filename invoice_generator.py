#!/usr/bin/env python3
"""
Générateur de factures EN16931 — UBL 2.1 / CII D16B
Supporte : factures (380), avoirs (381), factures correctives (384), notes de débit (389)
"""

import json
import sys
import argparse
from copy import deepcopy
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

def build_ubl(d: dict) -> str:
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
    _t(root, cbc("CustomizationID"), "urn:cen.eu:en16931:2017")           # BT-24
    _t(root, cbc("ID"),              inv["id"])                            # BT-1
    _t(root, cbc("IssueDate"),       inv["issue_date"])                    # BT-2
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
        _t(pts, cbc("CompanyID"), supplier["vat_id"])
        _t(ET.SubElement(pts, cac("TaxScheme")), cbc("ID"), "VAT")
    ple = ET.SubElement(sp, cac("PartyLegalEntity"))
    _t(ple, cbc("RegistrationName"), supplier["name"])
    _t(ple, cbc("CompanyID"), supplier.get("company_id", ""))
    if supplier.get("contact"):
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
        _t(pts2, cbc("CompanyID"), buyer["vat_id"])
        _t(ET.SubElement(pts2, cac("TaxScheme")), cbc("ID"), "VAT")
    ple2 = ET.SubElement(bp, cac("PartyLegalEntity"))
    _t(ple2, cbc("RegistrationName"), buyer["name"])
    _t(ple2, cbc("CompanyID"), buyer.get("company_id", ""))
    if buyer.get("contact"):
        _ubl_contact(bp, buyer["contact"], cac, cbc)

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
                fi  = ET.SubElement(fib, cac("FinancialInstitution"))
                _t(fi, cbc("ID"), payment["bic"])

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


# ── Générateur CII D16B ───────────────────────────────────────────────

def build_cii(d: dict) -> str:
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
    _t(gbpi, ram("ID"), "urn:cen.eu:en16931:2017")

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

    # Lignes
    for line in lines:
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
    if supplier.get("contact"):
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
    if inv.get("purchase_order_ref"):
        _t(ET.SubElement(hta, ram("BuyerOrderReferencedDocument")), ram("IssuerAssignedID"), inv["purchase_order_ref"])
    if inv.get("contract_ref"):
        _t(ET.SubElement(hta, ram("ContractReferencedDocument")), ram("IssuerAssignedID"), inv["contract_ref"])

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
    _t(sms, ram("LineTotalAmount"),     f"{float(totals['line_extension']):.2f}")  # BT-106
    if totals.get("charge_total", 0):
        _t(sms, ram("ChargeTotalAmount"),    f"{float(totals['charge_total']):.2f}")   # BT-108
    if totals.get("allowance_total", 0):
        _t(sms, ram("AllowanceTotalAmount"), f"{float(totals['allowance_total']):.2f}") # BT-107
    _t(sms, ram("TaxBasisTotalAmount"), f"{float(totals['tax_exclusive']):.2f}")   # BT-109
    _t(sms, ram("TaxTotalAmount"),      f"{float(totals['tax_amount']):.2f}")      # BT-110
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
        description="Générateur de factures EN16931 (UBL 2.1 / CII D16B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python invoice_generator.py invoice_example.json
  python invoice_generator.py invoice_example.json --format cii
  python invoice_generator.py invoice_example.json --format ubl -o ma_facture.xml
        """
    )
    parser.add_argument("config", help="Fichier JSON avec les champs variables")
    parser.add_argument("--format", choices=["ubl", "cii"], default="ubl",
                        help="Format de sortie : ubl (défaut) ou cii")
    parser.add_argument("-o", "--output", help="Chemin du fichier XML de sortie")
    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"Erreur : fichier introuvable : {args.config}", file=sys.stderr)
        sys.exit(1)

    data    = load_data(args.config)
    xml_str = build_ubl(data) if args.format == "ubl" else build_cii(data)

    inv_id = data["invoice"]["id"].replace(" ", "_").replace("/", "-")
    output = args.output or f"{inv_id}_{args.format}.xml"

    with open(output, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"Facture generee : {output}  ({args.format.upper()})")


if __name__ == "__main__":
    main()
