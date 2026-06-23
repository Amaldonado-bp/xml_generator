#!/usr/bin/env python3
"""
Validateur de factures électroniques — UBL 2.1 / CII D16B
Vérifie la conformité à la norme EN16931 / AFNOR XP Z12-014

Usage :
  python invoice_validator.py facture.xml
  python invoice_validator.py facture.xml --schemas ./schemas
  python invoice_validator.py facture.xml --format json
"""

import sys
import re
import json
import argparse
import io
from pathlib import Path

# Force UTF-8 sur la console Windows pour les caractères spéciaux
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

try:
    from lxml import etree as lxml_etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False

# ── Namespaces ─────────────────────────────────────────────────────────
NS_UBL_INV    = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_UBL_CREDIT = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
NS_UBL_CAC    = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_UBL_CBC    = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS_CII_RSM    = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
NS_CII_RAM    = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
NS_CII_UDT    = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

VALID_TYPE_CODES  = {"380", "381", "384", "389"}
ZERO_VAT_CATS     = {"E", "AE", "K", "G", "O", "Z"}
VALID_VAT_CATS    = {"S", "Z", "E", "AE", "K", "G", "O"}

# 1824 codes actifs UN/ECE Rec 20 (source : https://service.unece.org/trade/uncefact/vocabulary/rec20/)
VALID_UNIT_CODES = {
    "10", "11", "13", "14", "15", "1I", "20", "21", "22", "23", "24", "25",
    "27", "28", "2A", "2B", "2C", "2G", "2H", "2I", "2J", "2K", "2L", "2M",
    "2N", "2P", "2Q", "2R", "2U", "2X", "2Y", "2Z", "33", "34", "35", "37",
    "38", "3B", "3C", "40", "41", "4C", "4G", "4H", "4K", "4L", "4M", "4N",
    "4O", "4P", "4Q", "4R", "4T", "4U", "4W", "4X", "56", "57", "58", "59",
    "5A", "5B", "5E", "5J", "60", "61", "64", "66", "74", "76", "77", "78",
    "80", "81", "84", "85", "87", "89", "91", "A1", "A10", "A11", "A12", "A13",
    "A14", "A15", "A16", "A17", "A18", "A19", "A2", "A20", "A21", "A22", "A23", "A24",
    "A25", "A26", "A27", "A28", "A29", "A3", "A30", "A31", "A32", "A33", "A34", "A35",
    "A36", "A37", "A38", "A39", "A4", "A40", "A41", "A42", "A43", "A44", "A45", "A47",
    "A48", "A49", "A5", "A50", "A51", "A52", "A53", "A54", "A55", "A56", "A57", "A58",
    "A59", "A6", "A60", "A61", "A62", "A63", "A64", "A65", "A66", "A67", "A68", "A69",
    "A7", "A70", "A71", "A73", "A74", "A75", "A76", "A77", "A78", "A79", "A8", "A80",
    "A81", "A82", "A83", "A84", "A85", "A86", "A87", "A88", "A89", "A9", "A90", "A91",
    "A93", "A94", "A95", "A96", "A97", "A98", "A99", "AA", "AB", "ACR", "ACT", "AD",
    "AE", "AH", "AI", "AK", "AL", "AMH", "AMP", "ANN", "APZ", "AQ", "ARE", "AS",
    "ASM", "ASU", "ATM", "ATT", "AWG", "AY", "AZ", "B1", "B10", "B11", "B12", "B13",
    "B14", "B15", "B16", "B17", "B18", "B19", "B20", "B21", "B22", "B23", "B24", "B25",
    "B26", "B27", "B28", "B29", "B3", "B30", "B31", "B32", "B33", "B34", "B35", "B36",
    "B37", "B38", "B39", "B4", "B40", "B41", "B42", "B43", "B44", "B45", "B46", "B47",
    "B48", "B49", "B50", "B51", "B52", "B53", "B54", "B55", "B56", "B57", "B58", "B59",
    "B60", "B61", "B62", "B63", "B64", "B65", "B66", "B67", "B68", "B69", "B7", "B70",
    "B71", "B72", "B73", "B74", "B75", "B76", "B77", "B78", "B79", "B8", "B80", "B81",
    "B82", "B83", "B84", "B85", "B86", "B87", "B88", "B89", "B90", "B91", "B92", "B93",
    "B94", "B95", "B96", "B97", "B98", "B99", "BAR", "BB", "BFT", "BHP", "BIL", "BLD",
    "BLL", "BP", "BPM", "BQL", "BTU", "BUA", "BUI", "C0", "C10", "C11", "C12", "C13",
    "C14", "C15", "C16", "C17", "C18", "C19", "C20", "C21", "C22", "C23", "C24", "C25",
    "C26", "C27", "C28", "C29", "C3", "C30", "C31", "C32", "C33", "C34", "C35", "C36",
    "C37", "C38", "C39", "C40", "C41", "C42", "C43", "C44", "C45", "C46", "C47", "C48",
    "C49", "C50", "C51", "C52", "C53", "C54", "C55", "C56", "C57", "C58", "C59", "C60",
    "C61", "C62", "C63", "C64", "C65", "C66", "C67", "C68", "C69", "C7", "C70", "C71",
    "C72", "C73", "C74", "C75", "C76", "C78", "C79", "C8", "C80", "C81", "C82", "C83",
    "C84", "C85", "C86", "C87", "C88", "C89", "C9", "C90", "C91", "C92", "C93", "C94",
    "C95", "C96", "C97", "C99", "CCT", "CDL", "CEL", "CEN", "CG", "CGM", "CKG", "CLF",
    "CLT", "CMK", "CMQ", "CMT", "CNP", "CNT", "COU", "CTG", "CTM", "CTN", "CUR", "CWA",
    "CWI", "D03", "D04", "D1", "D10", "D11", "D12", "D13", "D15", "D16", "D17", "D18",
    "D19", "D2", "D20", "D21", "D22", "D23", "D24", "D25", "D26", "D27", "D29", "D30",
    "D31", "D32", "D33", "D34", "D35", "D36", "D37", "D38", "D39", "D41", "D42", "D43",
    "D44", "D45", "D46", "D47", "D48", "D49", "D5", "D50", "D51", "D52", "D53", "D54",
    "D55", "D56", "D57", "D58", "D59", "D6", "D60", "D61", "D62", "D63", "D65", "D68",
    "D69", "D70", "D71", "D72", "D73", "D74", "D75", "D76", "D77", "D78", "D80", "D81",
    "D82", "D83", "D85", "D86", "D87", "D88", "D89", "D9", "D91", "D93", "D94", "D95",
    "DAA", "DAD", "DAY", "DB", "DBM", "DBW", "DD", "DEC", "DG", "DJ", "DLT", "DMA",
    "DMK", "DMO", "DMQ", "DMT", "DN", "DPC", "DPR", "DPT", "DRA", "DRI", "DRL", "DT",
    "DTN", "DU", "DWT", "DX", "DZN", "DZP", "E01", "E07", "E08", "E09", "E10", "E11",
    "E12", "E14", "E15", "E16", "E17", "E18", "E19", "E20", "E21", "E22", "E23", "E25",
    "E27", "E28", "E30", "E31", "E32", "E33", "E34", "E35", "E36", "E37", "E38", "E39",
    "E4", "E40", "E41", "E42", "E43", "E44", "E45", "E46", "E47", "E48", "E49", "E50",
    "E51", "E52", "E53", "E54", "E55", "E56", "E57", "E58", "E59", "E60", "E61", "E62",
    "E63", "E64", "E65", "E66", "E67", "E68", "E69", "E70", "E71", "E72", "E73", "E74",
    "E75", "E76", "E77", "E78", "E79", "E80", "E81", "E82", "E83", "E84", "E85", "E86",
    "E87", "E88", "E89", "E90", "E91", "E92", "E93", "E94", "E95", "E96", "E97", "E98",
    "E99", "EA", "EB", "EQ", "F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08",
    "F10", "F11", "F12", "F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20", "F21",
    "F22", "F23", "F24", "F25", "F26", "F27", "F28", "F29", "F30", "F31", "F32", "F33",
    "F34", "F35", "F36", "F37", "F38", "F39", "F40", "F41", "F42", "F43", "F44", "F45",
    "F46", "F47", "F48", "F49", "F50", "F51", "F52", "F53", "F54", "F55", "F56", "F57",
    "F58", "F59", "F60", "F61", "F62", "F63", "F64", "F65", "F66", "F67", "F68", "F69",
    "F70", "F71", "F72", "F73", "F74", "F75", "F76", "F77", "F78", "F79", "F80", "F81",
    "F82", "F83", "F84", "F85", "F86", "F87", "F88", "F89", "F90", "F91", "F92", "F93",
    "F94", "F95", "F96", "F97", "F98", "F99", "FAH", "FAR", "FBM", "FC", "FF", "FH",
    "FIT", "FL", "FNU", "FOT", "FP", "FR", "FS", "FTK", "FTQ", "G01", "G04", "G05",
    "G06", "G08", "G09", "G10", "G11", "G12", "G13", "G14", "G15", "G16", "G17", "G18",
    "G19", "G2", "G20", "G21", "G23", "G24", "G25", "G26", "G27", "G28", "G29", "G3",
    "G30", "G31", "G32", "G33", "G34", "G35", "G36", "G37", "G38", "G39", "G40", "G41",
    "G42", "G43", "G44", "G45", "G46", "G47", "G48", "G49", "G50", "G51", "G52", "G53",
    "G54", "G55", "G56", "G57", "G58", "G59", "G60", "G61", "G62", "G63", "G64", "G65",
    "G66", "G67", "G68", "G69", "G70", "G71", "G72", "G73", "G74", "G75", "G76", "G77",
    "G78", "G79", "G80", "G81", "G82", "G83", "G84", "G85", "G86", "G87", "G88", "G89",
    "G90", "G91", "G92", "G93", "G94", "G95", "G96", "G97", "G98", "G99", "GB", "GBQ",
    "GDW", "GE", "GF", "GFI", "GGR", "GIA", "GIC", "GII", "GIP", "GJ", "GL", "GLD",
    "GLI", "GLL", "GM", "GO", "GP", "GQ", "GRM", "GRN", "GRO", "GRT", "GT", "GV",
    "GWH", "H03", "H04", "H05", "H06", "H07", "H08", "H09", "H10", "H11", "H12", "H13",
    "H14", "H15", "H16", "H18", "H19", "H20", "H21", "H22", "H23", "H24", "H25", "H26",
    "H27", "H28", "H29", "H30", "H31", "H32", "H33", "H34", "H35", "H36", "H37", "H38",
    "H39", "H40", "H41", "H42", "H43", "H44", "H45", "H46", "H47", "H48", "H49", "H50",
    "H51", "H52", "H53", "H54", "H55", "H56", "H57", "H58", "H59", "H60", "H61", "H62",
    "H63", "H64", "H65", "H66", "H67", "H68", "H69", "H70", "H71", "H72", "H73", "H74",
    "H75", "H76", "H77", "H78", "H79", "H80", "H81", "H82", "H83", "H84", "H85", "H87",
    "H88", "H89", "H90", "H91", "H92", "H93", "H94", "H95", "H96", "H98", "H99", "HA",
    "HAD", "HAR", "HBA", "HBX", "HC", "HDW", "HEA", "HGM", "HH", "HIU", "HJ", "HKM",
    "HLT", "HM", "HMO", "HMQ", "HMT", "HN", "HP", "HPA", "HTZ", "HUR", "HWE", "IA",
    "IE", "INH", "INK", "INQ", "ISD", "IU", "IUG", "IV", "J10", "J12", "J13", "J14",
    "J15", "J16", "J17", "J18", "J19", "J2", "J20", "J21", "J22", "J23", "J24", "J25",
    "J26", "J27", "J28", "J29", "J30", "J31", "J32", "J33", "J34", "J35", "J36", "J38",
    "J39", "J40", "J41", "J42", "J43", "J44", "J45", "J46", "J47", "J48", "J49", "J50",
    "J51", "J52", "J53", "J54", "J55", "J56", "J57", "J58", "J59", "J60", "J61", "J62",
    "J63", "J64", "J65", "J66", "J67", "J68", "J69", "J70", "J71", "J72", "J73", "J74",
    "J75", "J76", "J78", "J79", "J81", "J82", "J83", "J84", "J85", "J87", "J89", "J90",
    "J91", "J92", "J93", "J94", "J95", "J96", "J97", "J98", "J99", "JE", "JK", "JM",
    "JNT", "JOU", "JPS", "JWL", "K1", "K10", "K11", "K12", "K13", "K14", "K15", "K16",
    "K17", "K18", "K19", "K2", "K20", "K21", "K22", "K23", "K24", "K25", "K26", "K27",
    "K28", "K3", "K30", "K31", "K32", "K33", "K34", "K35", "K36", "K37", "K38", "K39",
    "K40", "K41", "K42", "K43", "K45", "K46", "K47", "K48", "K49", "K5", "K50", "K51",
    "K52", "K53", "K54", "K55", "K58", "K59", "K6", "K60", "K61", "K62", "K63", "K64",
    "K65", "K66", "K67", "K68", "K69", "K70", "K71", "K73", "K74", "K75", "K76", "K77",
    "K78", "K79", "K80", "K81", "K82", "K83", "K84", "K85", "K86", "K87", "K88", "K89",
    "K90", "K91", "K92", "K93", "K94", "K95", "K96", "K97", "K98", "K99", "KA", "KAT",
    "KB", "KBA", "KCC", "KDW", "KEL", "KGM", "KGS", "KHY", "KHZ", "KI", "KIC", "KIP",
    "KJ", "KJO", "KL", "KLK", "KLX", "KMA", "KMH", "KMK", "KMQ", "KMT", "KNI", "KNM",
    "KNS", "KNT", "KO", "KPA", "KPH", "KPO", "KPP", "KR", "KSD", "KSH", "KT", "KTN",
    "KUR", "KVA", "KVR", "KVT", "KW", "KWH", "KWN", "KWO", "KWS", "KWT", "KWY", "KX",
    "L10", "L11", "L12", "L13", "L14", "L15", "L16", "L17", "L18", "L19", "L2", "L20",
    "L21", "L23", "L24", "L25", "L26", "L27", "L28", "L29", "L30", "L31", "L32", "L33",
    "L34", "L35", "L36", "L37", "L38", "L39", "L40", "L41", "L42", "L43", "L44", "L45",
    "L46", "L47", "L48", "L49", "L50", "L51", "L52", "L53", "L54", "L55", "L56", "L57",
    "L58", "L59", "L60", "L63", "L64", "L65", "L66", "L67", "L68", "L69", "L70", "L71",
    "L72", "L73", "L74", "L75", "L76", "L77", "L78", "L79", "L80", "L81", "L82", "L83",
    "L84", "L85", "L86", "L87", "L88", "L89", "L90", "L91", "L92", "L93", "L94", "L95",
    "L96", "L98", "L99", "LA", "LAC", "LBR", "LBT", "LD", "LEF", "LF", "LH", "LK",
    "LM", "LN", "LO", "LP", "LPA", "LR", "LS", "LTN", "LTR", "LUB", "LUM", "LUX",
    "LY", "M1", "M10", "M11", "M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19",
    "M20", "M21", "M22", "M23", "M24", "M25", "M26", "M27", "M29", "M30", "M31", "M32",
    "M33", "M34", "M35", "M36", "M37", "M38", "M39", "M4", "M40", "M41", "M42", "M43",
    "M44", "M45", "M46", "M47", "M48", "M49", "M5", "M50", "M51", "M52", "M53", "M55",
    "M56", "M57", "M58", "M59", "M60", "M61", "M62", "M63", "M64", "M65", "M66", "M67",
    "M68", "M69", "M7", "M70", "M71", "M72", "M73", "M74", "M75", "M76", "M77", "M78",
    "M79", "M80", "M81", "M82", "M83", "M84", "M85", "M86", "M87", "M88", "M89", "M9",
    "M90", "M91", "M92", "M93", "M94", "M95", "M96", "M97", "M98", "M99", "MAH", "MAL",
    "MAM", "MAR", "MAW", "MBE", "MBF", "MBR", "MC", "MCU", "MD", "MGM", "MHZ", "MIK",
    "MIL", "MIN", "MIO", "MIU", "MKD", "MKM", "MKW", "MLD", "MLT", "MMK", "MMQ", "MMT",
    "MND", "MON", "MPA", "MQD", "MQH", "MQM", "MQS", "MQW", "MRD", "MRM", "MRW", "MSK",
    "MTK", "MTQ", "MTR", "MTS", "MVA", "MWH", "N1", "N10", "N11", "N12", "N13", "N14",
    "N15", "N16", "N17", "N18", "N19", "N20", "N21", "N22", "N23", "N24", "N25", "N26",
    "N27", "N28", "N29", "N3", "N30", "N31", "N32", "N33", "N34", "N35", "N36", "N37",
    "N38", "N39", "N40", "N41", "N42", "N43", "N44", "N45", "N46", "N47", "N48", "N49",
    "N50", "N51", "N52", "N53", "N54", "N55", "N56", "N57", "N58", "N59", "N60", "N61",
    "N62", "N63", "N64", "N65", "N66", "N67", "N68", "N69", "N70", "N71", "N72", "N73",
    "N74", "N75", "N76", "N77", "N78", "N79", "N80", "N81", "N82", "N83", "N84", "N85",
    "N86", "N87", "N88", "N89", "N90", "N91", "N92", "N93", "N94", "N95", "N96", "N97",
    "N98", "N99", "NA", "NAR", "NCL", "NEW", "NF", "NIL", "NIU", "NL", "NM3", "NMI",
    "NMP", "NPR", "NPT", "NQ", "NR", "NT", "NTT", "NTU", "NU", "NX", "OA", "ODE",
    "ODG", "ODK", "ODM", "OHM", "ON", "ONZ", "OPM", "OT", "OZ", "OZA", "OZI", "P1",
    "P10", "P11", "P12", "P13", "P14", "P15", "P16", "P17", "P18", "P19", "P2", "P20",
    "P21", "P22", "P23", "P24", "P25", "P26", "P27", "P28", "P29", "P30", "P31", "P32",
    "P33", "P34", "P35", "P36", "P37", "P38", "P39", "P40", "P41", "P42", "P43", "P44",
    "P45", "P46", "P47", "P48", "P49", "P5", "P50", "P51", "P52", "P53", "P54", "P55",
    "P56", "P57", "P58", "P59", "P60", "P61", "P62", "P63", "P64", "P65", "P66", "P67",
    "P68", "P69", "P70", "P71", "P72", "P73", "P74", "P75", "P76", "P77", "P78", "P79",
    "P80", "P81", "P82", "P83", "P84", "P85", "P86", "P87", "P88", "P89", "P90", "P91",
    "P92", "P93", "P94", "P95", "P96", "P97", "P98", "P99", "PAL", "PD", "PFL", "PGL",
    "PI", "PLA", "PO", "PQ", "PR", "PS", "PT", "PTD", "PTI", "PTL", "PTN", "Q10",
    "Q11", "Q12", "Q13", "Q14", "Q15", "Q16", "Q17", "Q18", "Q19", "Q20", "Q21", "Q22",
    "Q23", "Q24", "Q25", "Q26", "Q27", "Q28", "Q29", "Q3", "Q30", "Q31", "Q32", "Q33",
    "Q34", "Q35", "Q36", "Q37", "Q38", "Q39", "Q40", "Q41", "Q42", "QA", "QAN", "QB",
    "QR", "QT", "QTD", "QTI", "QTL", "QTR", "R1", "R9", "RH", "RM", "ROM", "RP",
    "RPM", "RPS", "RT", "S3", "S4", "SAN", "SCO", "SCR", "SEC", "SET", "SG", "SHT",
    "SIE", "SM3", "SMI", "SQ", "SQR", "SR", "STC", "STI", "STK", "STL", "STN", "STW",
    "SW", "SX", "SYR", "T0", "T3", "TAH", "TAN", "TI", "TIC", "TIP", "TKM", "TMS",
    "TNE", "TP", "TPI", "TPR", "TQD", "TRL", "TST", "TTS", "U1", "U2", "UA", "UB",
    "UC", "VA", "VLT", "VP", "W2", "WA", "WB", "WCD", "WE", "WEB", "WEE", "WG",
    "WHR", "WM", "WSD", "WTT", "WW", "X1", "YDK", "YDQ", "YRD", "Z11", "ZP", "ZZ",
}

# Codes pays ISO 3166-1 alpha-2
ISO_COUNTRIES = {
    "AF","AX","AL","DZ","AS","AD","AO","AI","AQ","AG","AR","AM","AW","AU","AT","AZ",
    "BS","BH","BD","BB","BY","BE","BZ","BJ","BM","BT","BO","BQ","BA","BW","BV","BR",
    "IO","BN","BG","BF","BI","CV","KH","CM","CA","KY","CF","TD","CL","CN","CX","CC",
    "CO","KM","CG","CD","CK","CR","CI","HR","CU","CW","CY","CZ","DK","DJ","DM","DO",
    "EC","EG","SV","GQ","ER","EE","SZ","ET","FK","FO","FJ","FI","FR","GF","PF","TF",
    "GA","GM","GE","DE","GH","GI","GR","GL","GD","GP","GU","GT","GG","GN","GW","GY",
    "HT","HM","VA","HN","HK","HU","IS","IN","ID","IR","IQ","IE","IM","IL","IT","JM",
    "JP","JE","JO","KZ","KE","KI","KP","KR","KW","KG","LA","LV","LB","LS","LR","LY",
    "LI","LT","LU","MO","MG","MW","MY","MV","ML","MT","MH","MQ","MR","MU","YT","MX",
    "FM","MD","MC","MN","ME","MS","MA","MZ","MM","NA","NR","NP","NL","NC","NZ","NI",
    "NE","NG","NU","NF","MK","MP","NO","OM","PK","PW","PS","PA","PG","PY","PE","PH",
    "PN","PL","PT","PR","QA","RE","RO","RU","RW","BL","SH","KN","LC","MF","PM","VC",
    "WS","SM","ST","SA","SN","RS","SC","SL","SG","SX","SK","SI","SB","SO","ZA","GS",
    "SS","ES","LK","SD","SR","SJ","SE","CH","SY","TW","TJ","TZ","TH","TL","TG","TK",
    "TO","TT","TN","TR","TM","TC","TV","UG","UA","AE","GB","US","UM","UY","UZ","VU",
    "VE","VN","VG","VI","WF","EH","YE","ZM","ZW",
}


# ── Modèle d'anomalie ─────────────────────────────────────────────────

@dataclass
class Issue:
    severity: str   # ERROR | WARNING
    code: str       # ex : BR-02, BR-CO-10
    message: str
    location: str = ""

    def __str__(self):
        loc = f"  [{self.location}]" if self.location else ""
        return f"[{self.severity}] {self.code} : {self.message}{loc}"


# ── Utilitaires ───────────────────────────────────────────────────────

def _d(val, default=Decimal("0.00")) -> Decimal:
    """Convertit une valeur en Decimal (arrondi 2 décimales)."""
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return default


def _txt(el, path: str) -> str:
    """Retourne le texte du premier enfant correspondant au chemin, ou ''."""
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _check_date_yyyymmdd(date_str: str, bt: str) -> Optional[str]:
    """Retourne un message d'erreur si la date AAAA-MM-JJ est invalide."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return f"{bt} : Format de date invalide '{date_str}' (attendu AAAA-MM-JJ)"
    try:
        y, m, d = map(int, date_str.split("-"))
        if not (1 <= m <= 12):
            return f"{bt} : Mois invalide ({m})"
        if not (1 <= d <= 31):
            return f"{bt} : Jour invalide ({d})"
    except Exception:
        return f"{bt} : Date invalide '{date_str}'"
    return None


# ── Validateur principal ──────────────────────────────────────────────

class InvoiceValidator:
    def __init__(self, xml_path: str, schema_dir: Optional[str] = None):
        self.xml_path   = xml_path
        self.schema_dir = Path(schema_dir) if schema_dir else None
        self.issues: list = []
        self.fmt: Optional[str] = None  # 'UBL' | 'CII'
        self.type_code: Optional[str] = None
        self.root: Optional[ET.Element] = None
        self._cac = f"{{{NS_UBL_CAC}}}"
        self._cbc = f"{{{NS_UBL_CBC}}}"
        self._ram = f"{{{NS_CII_RAM}}}"
        self._rsm = f"{{{NS_CII_RSM}}}"
        self._udt = f"{{{NS_CII_UDT}}}"

    def _err(self, code, msg, loc=""):
        self.issues.append(Issue("ERROR", code, msg, loc))

    def _warn(self, code, msg, loc=""):
        self.issues.append(Issue("WARNING", code, msg, loc))

    # ── Point d'entrée ───────────────────────────────────────────────

    def validate(self) -> list:
        """Lance toutes les vérifications et retourne la liste des anomalies."""

        # 1. Bien-formedness XML
        try:
            tree = ET.parse(self.xml_path)
            self.root = tree.getroot()
        except ET.ParseError as e:
            self._err("WF-01", f"XML malformé : {e}")
            return self.issues
        except FileNotFoundError:
            self._err("WF-00", f"Fichier introuvable : {self.xml_path}")
            return self.issues

        # 2. Détection du format
        tag = self.root.tag
        if NS_UBL_INV in tag or NS_UBL_CREDIT in tag:
            self.fmt = "UBL"
        elif NS_CII_RSM in tag:
            self.fmt = "CII"
        else:
            self._err("FMT-01", f"Format non reconnu. Espace de noms racine : {tag}")
            return self.issues

        # 3. Validation XSD (optionnelle, via lxml)
        if HAS_LXML:
            self._xsd_validate()
        else:
            self._warn("XSD-00",
                "lxml non installé — validation XSD ignorée. "
                "Installez-le avec : pip install lxml")

        # 4. Règles métier EN16931
        if self.fmt == "UBL":
            self._validate_ubl()
        else:
            self._validate_cii()

        return self.issues

    # ── Validation XSD (lxml) ────────────────────────────────────────

    def _xsd_validate(self):
        if not self.schema_dir:
            return
        schema_name = (
            "UBL-Invoice-2.1.xsd"
            if self.fmt == "UBL"
            else "CrossIndustryInvoice_100pD16B.xsd"
        )
        schema_path = self.schema_dir / schema_name
        if not schema_path.exists():
            self._warn("XSD-01", f"Schéma XSD non trouvé : {schema_path}")
            return
        try:
            schema_doc = lxml_etree.parse(str(schema_path))
            schema     = lxml_etree.XMLSchema(schema_doc)
            doc        = lxml_etree.parse(self.xml_path)
            if not schema.validate(doc):
                for e in schema.error_log:
                    self._err("XSD", e.message, f"ligne {e.line}")
        except Exception as e:
            self._warn("XSD-02", f"Erreur lors de la validation XSD : {e}")

    # ── Validation UBL 2.1 ───────────────────────────────────────────

    def _validate_ubl(self):
        r   = self.root
        cac = self._cac
        cbc = self._cbc

        # BR-01 — CustomizationID (BT-24)
        cid = _txt(r, f"{cbc}CustomizationID")
        if not cid:
            self._err("BR-01", "BT-24 : L'identifiant de spécification (CustomizationID) est obligatoire")
        elif "en16931" not in cid:
            self._warn("BR-01", f"BT-24 : CustomizationID '{cid}' ne référence pas en16931")

        # BR-02 — ID facture (BT-1)
        if not _txt(r, f"{cbc}ID"):
            self._err("BR-02", "BT-1 : Le numéro de facture est obligatoire")

        # BR-03 — Date d'émission (BT-2)
        issue_date = _txt(r, f"{cbc}IssueDate")
        if not issue_date:
            self._err("BR-03", "BT-2 : La date d'émission est obligatoire")
        else:
            err = _check_date_yyyymmdd(issue_date, "BT-2")
            if err:
                self._err("BR-03", err)

        # BR-04 — Code type (BT-3)
        type_code = _txt(r, f"{cbc}InvoiceTypeCode") or _txt(r, f"{cbc}CreditNoteTypeCode")
        self.type_code = type_code
        if not type_code:
            self._err("BR-04", "BT-3 : Le code de type de document est obligatoire")
        elif type_code not in VALID_TYPE_CODES:
            self._err("BR-04",
                f"BT-3 : Code type invalide '{type_code}' "
                f"(valeurs acceptées : {', '.join(sorted(VALID_TYPE_CODES))})")

        # BR-05 — Code monnaie (BT-5)
        currency = _txt(r, f"{cbc}DocumentCurrencyCode")
        if not currency:
            self._err("BR-05", "BT-5 : Le code monnaie est obligatoire")
        elif not re.match(r"^[A-Z]{3}$", currency):
            self._err("BR-05", f"BT-5 : Code monnaie invalide '{currency}' (format ISO 4217, ex : EUR)")

        # BT-9 — Date d'échéance (si présente)
        due_hdr = _txt(r, f"{cbc}DueDate")
        due_pm  = _txt(r, f"{cac}PaymentMeans/{cbc}PaymentDueDate")
        for due_val, label in [(due_hdr, "BT-9 (DueDate)"), (due_pm, "BT-9 (PaymentDueDate)")]:
            if due_val:
                err = _check_date_yyyymmdd(due_val, label)
                if err:
                    self._err("BR-03", err)

        # BT-10 — Référence acheteur
        if not _txt(r, f"{cbc}BuyerReference"):
            self._warn("BT-10",
                "BT-10 : La référence acheteur est recommandée (obligatoire pour Chorus Pro / Coupa)")

        # BG-4 — Fournisseur
        sup = r.find(f"{cac}AccountingSupplierParty/{cac}Party")
        if sup is None:
            self._err("BR-06", "BG-4 : Le fournisseur (AccountingSupplierParty) est obligatoire")
        else:
            self._validate_ubl_party(sup, "fournisseur", "BG-4", "BR-06", "BT-27",
                                     addr_code="BR-11", country_code="BR-12",
                                     country_bt="BT-40")

        # BG-7 — Acheteur
        buy = r.find(f"{cac}AccountingCustomerParty/{cac}Party")
        if buy is None:
            self._err("BR-07", "BG-7 : L'acheteur (AccountingCustomerParty) est obligatoire")
        else:
            self._validate_ubl_party(buy, "acheteur", "BG-7", "BR-07", "BT-44",
                                     addr_code=None, country_code="BR-13",
                                     country_bt="BT-55")

        # BG-23 — TVA totale
        tt = r.find(f"{cac}TaxTotal")
        if tt is None:
            self._err("BR-45", "BG-23 : Le bloc TVA (TaxTotal) est obligatoire")
        else:
            self._validate_ubl_tax_total(r, tt, currency)

        # BG-22 — Totaux
        lmt = r.find(f"{cac}LegalMonetaryTotal")
        if lmt is None:
            self._err("BR-12", "BG-22 : Les totaux monétaires (LegalMonetaryTotal) sont obligatoires")
        else:
            self._validate_ubl_totals(r, lmt, currency)

        # BG-25 — Lignes
        lines = r.findall(f"{cac}InvoiceLine")
        if not lines:
            lines = r.findall(f"{cac}CreditNoteLine")
        if not lines:
            self._err("BR-16", "BG-25 : Au moins une ligne de facture est obligatoire")
        else:
            for i, line in enumerate(lines, 1):
                self._validate_ubl_line(line, i, currency)

        # Vérification des montants positifs pour les avoirs (BT-3 = 381)
        if self.type_code == "381":
            self._check_credit_note_amounts_ubl(r)

    def _check_credit_note_amounts_ubl(self, r):
        cac, cbc = self._cac, self._cbc

        # Totaux globaux
        amount_checks = [
            (f"{cac}LegalMonetaryTotal/{cbc}LineExtensionAmount",  "BT-106", "LineExtensionAmount"),
            (f"{cac}LegalMonetaryTotal/{cbc}TaxExclusiveAmount",   "BT-109", "TaxExclusiveAmount"),
            (f"{cac}LegalMonetaryTotal/{cbc}TaxInclusiveAmount",   "BT-112", "TaxInclusiveAmount"),
            (f"{cac}LegalMonetaryTotal/{cbc}PayableAmount",        "BT-115", "PayableAmount"),
            (f"{cac}TaxTotal/{cbc}TaxAmount",                      "BT-110", "TaxAmount"),
        ]
        for path, bt, label in amount_checks:
            el = r.find(path)
            if el is not None and el.text:
                val = _d(el.text)
                if val < Decimal("0"):
                    self._err("BR-AV-01",
                        f"{bt} : Dans un avoir (381), {label} doit être positif ou nul (reçu : {val})")

        # Montants de lignes
        lines = r.findall(f"{cac}InvoiceLine") or r.findall(f"{cac}CreditNoteLine")
        for i, line in enumerate(lines, 1):
            el = line.find(f"{cbc}LineExtensionAmount")
            if el is not None and el.text:
                val = _d(el.text)
                if val < Decimal("0"):
                    self._err("BR-AV-01",
                        f"BT-131 : Dans un avoir (381), le montant net de ligne doit être positif ou nul "
                        f"(reçu : {val})", f"Ligne {i}")

    def _validate_ubl_party(self, party, label, bg, name_code, name_bt,
                             addr_code, country_code, country_bt):
        cac, cbc = self._cac, self._cbc

        # Nom
        name = _txt(party, f"{cac}PartyName/{cbc}Name")
        if not name:
            self._err(name_code, f"{name_bt} : Le nom du {label} est obligatoire")

        # Adresse postale
        addr = party.find(f"{cac}PostalAddress")
        if addr is None:
            if addr_code:
                self._err(addr_code, f"{bg} : L'adresse postale du {label} est obligatoire")
        else:
            country_el = addr.find(f"{cac}Country/{cbc}IdentificationCode")
            country    = (country_el.text or "").strip() if country_el is not None else ""
            if not country:
                self._err(country_code,
                    f"{country_bt} : Le code pays du {label} est obligatoire")
            elif country not in ISO_COUNTRIES:
                self._err(country_code,
                    f"{country_bt} : Code pays invalide '{country}' (ISO 3166-1 alpha-2)")

            if label == "fournisseur":
                if not _txt(addr, f"{cbc}StreetName"):
                    self._warn("BT-35", "BT-35 : La rue du fournisseur est recommandée")
                if not _txt(addr, f"{cbc}CityName"):
                    self._warn("BT-37", "BT-37 : La ville du fournisseur est recommandée")
                if not _txt(addr, f"{cbc}PostalZone"):
                    self._warn("BT-38", "BT-38 : Le code postal du fournisseur est recommandé")

        # Numéro TVA
        vat_el = party.find(f"{cac}PartyTaxScheme/{cbc}CompanyID")
        if vat_el is not None and vat_el.text:
            vat = vat_el.text.strip()
            if not re.match(r"^[A-Z]{2}[0-9A-Z]{2,12}$", vat):
                self._warn("BT-31",
                    f"BT-31/BT-48 : Format du numéro TVA {label} suspect : '{vat}' "
                    f"(attendu ex : FR07433927332)")

        # Identifiant légal (SIREN/SIRET)
        ple = party.find(f"{cac}PartyLegalEntity/{cbc}CompanyID")
        if label == "fournisseur" and (ple is None or not (ple.text or "").strip()):
            self._warn("BT-30", "BT-30 : L'identifiant légal du fournisseur (SIREN) est recommandé")

    def _validate_ubl_tax_total(self, root, tt, currency):
        cac, cbc = self._cac, self._cbc

        # Devise du TaxAmount
        ta_el = tt.find(f"{cbc}TaxAmount")
        if ta_el is None:
            self._err("BR-45", "BG-23 : TaxAmount est obligatoire dans TaxTotal")
            return
        cur_id = ta_el.get("currencyID", "")
        if cur_id and cur_id != currency:
            self._err("BR-53",
                f"BG-23 : Devise du TaxAmount ({cur_id}) ≠ DocumentCurrencyCode ({currency})")
        total_tax = _d(ta_el.text)

        # TaxSubtotals
        subtotals = tt.findall(f"{cac}TaxSubtotal")
        if not subtotals:
            self._err("BR-45", "BG-23 : Au moins un TaxSubtotal est obligatoire")
            return

        calc_total = Decimal("0.00")
        for i, sub in enumerate(subtotals, 1):
            taxable  = _d(_txt(sub, f"{cbc}TaxableAmount"))
            tax_amt  = _d(_txt(sub, f"{cbc}TaxAmount"))
            tc       = sub.find(f"{cac}TaxCategory")
            cat      = _txt(tc, f"{cbc}ID") if tc is not None else ""
            rate     = _d(_txt(tc, f"{cbc}Percent")) if tc is not None else Decimal("0")
            scheme   = _txt(tc, f"{cac}TaxScheme/{cbc}ID") if tc is not None else ""

            if not cat:
                self._err("BR-46", f"BG-23 / TaxSubtotal {i} : Le code catégorie TVA est obligatoire")
            elif cat not in VALID_VAT_CATS:
                self._err("BR-46",
                    f"BG-23 / TaxSubtotal {i} : Catégorie TVA invalide '{cat}' "
                    f"(valeurs : {', '.join(sorted(VALID_VAT_CATS))})")

            if scheme and scheme != "VAT":
                self._warn("BT-118", f"BG-23 / TaxSubtotal {i} : TaxScheme/{scheme} inattendu (attendu 'VAT')")

            if cat in ZERO_VAT_CATS and rate != Decimal("0"):
                self._err(f"BR-{cat}-08",
                    f"BG-23 / TaxSubtotal {i} : Taux TVA doit être 0% pour catégorie {cat} (reçu {rate}%)")

            # Vérification du calcul TVA
            if rate > Decimal("0"):
                expected = (taxable * rate / Decimal("100")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                if abs(tax_amt - expected) > Decimal("0.02"):
                    self._warn("BR-CO-17",
                        f"BG-23 / TaxSubtotal {i} : Montant TVA déclaré ({tax_amt}) ≠ "
                        f"base imposable ({taxable}) × taux ({rate}%) / 100 = {expected}")

            calc_total += tax_amt

        if abs(total_tax - calc_total) > Decimal("0.02"):
            self._warn("BR-CO-12",
                f"BG-23 : TaxAmount total ({total_tax}) ≠ somme des sous-totaux TVA ({calc_total})")

    def _validate_ubl_totals(self, root, lmt, currency):
        cac, cbc = self._cac, self._cbc

        def amt(tag):
            el = lmt.find(f"{cbc}{tag}")
            return _d(el.text if el is not None else "0")

        line_ext   = amt("LineExtensionAmount")    # BT-106
        allowances = amt("AllowanceTotalAmount")   # BT-107
        charges    = amt("ChargeTotalAmount")      # BT-108
        tax_excl   = amt("TaxExclusiveAmount")     # BT-109
        tax_incl   = amt("TaxInclusiveAmount")     # BT-112
        prepaid    = amt("PrepaidAmount")          # BT-113
        payable    = amt("PayableAmount")          # BT-115

        # TaxAmount depuis TaxTotal
        tt_el  = root.find(f"{cac}TaxTotal/{cbc}TaxAmount")
        tax_amt = _d(tt_el.text if tt_el is not None else "0")

        # BR-CO-10 : BT-106 = somme des montants nets lignes
        lines     = root.findall(f"{cac}InvoiceLine") or root.findall(f"{cac}CreditNoteLine")
        line_amts = [_d(_txt(l, f"{cbc}LineExtensionAmount")) for l in lines]
        sum_lines = sum(line_amts)
        if abs(line_ext - sum_lines) > Decimal("0.02"):
            detail = " + ".join(str(a) for a in line_amts)
            self._err("BR-CO-10",
                f"BT-106 : LineExtensionAmount ({line_ext}) ≠ somme des montants nets lignes ({detail} = {sum_lines})")

        # BR-CO-11 : BT-109 = BT-106 − BT-107 + BT-108
        expected_excl = line_ext - allowances + charges
        if abs(tax_excl - expected_excl) > Decimal("0.02"):
            self._err("BR-CO-11",
                f"BT-109 : TaxExclusiveAmount ({tax_excl}) ≠ "
                f"BT-106 ({line_ext}) − BT-107 ({allowances}) + BT-108 ({charges}) = {expected_excl}")

        # BR-CO-13 : BT-112 = BT-109 + BT-110
        expected_incl = tax_excl + tax_amt
        if abs(tax_incl - expected_incl) > Decimal("0.02"):
            self._err("BR-CO-13",
                f"BT-112 : TaxInclusiveAmount ({tax_incl}) ≠ "
                f"BT-109 ({tax_excl}) + BT-110 ({tax_amt}) = {expected_incl}")

        # BR-CO-16 : BT-115 = BT-112 − BT-113
        expected_payable = tax_incl - prepaid
        if abs(payable - expected_payable) > Decimal("0.02"):
            self._err("BR-CO-16",
                f"BT-115 : PayableAmount ({payable}) ≠ "
                f"BT-112 ({tax_incl}) − BT-113 ({prepaid}) = {expected_payable}")

    def _validate_ubl_line(self, line, idx, currency):
        cac, cbc = self._cac, self._cbc
        loc = f"Ligne {idx}"

        # BT-126 — ID ligne
        if not _txt(line, f"{cbc}ID"):
            self._err("BR-21", "BT-126 : L'identifiant de ligne est obligatoire", loc)

        # BT-129/130 — Quantité + unité
        qty_el = line.find(f"{cbc}InvoicedQuantity")
        if qty_el is None:
            qty_el = line.find(f"{cbc}CreditedQuantity")
        if qty_el is None or not (qty_el.text or "").strip():
            self._err("BR-22", "BT-129 : La quantité facturée est obligatoire", loc)
        else:
            unit = qty_el.get("unitCode", "")
            if not unit:
                self._err("BR-23", "BT-130 : Le code unité (unitCode) est obligatoire sur la quantité", loc)
            elif unit not in VALID_UNIT_CODES:
                self._warn("BR-23",
                    f"BT-130 : Code unité '{unit}' non reconnu par UN/ECE Rec 20/21 "
                    f"(ex : C62=pièce, KGM=kg, LTR=litre, HUR=heure, DAY=jour, MTR=mètre)", loc)

        # BT-131 — Montant net ligne
        net_el = line.find(f"{cbc}LineExtensionAmount")
        if net_el is None:
            self._err("BR-24", "BT-131 : Le montant net de ligne est obligatoire", loc)

        # BG-31 — Article
        item = line.find(f"{cac}Item")
        if item is None:
            self._err("BR-25", "BG-31 : La section Item est obligatoire", loc)
        else:
            if not _txt(item, f"{cbc}Name"):
                self._err("BR-25", "BT-153 : Le nom de l'article est obligatoire", loc)

            tc = item.find(f"{cac}ClassifiedTaxCategory")
            if tc is None:
                self._err("BR-26", "BG-30 : La catégorie TVA de ligne est obligatoire", loc)
            else:
                cat  = _txt(tc, f"{cbc}ID")
                rate = _d(_txt(tc, f"{cbc}Percent"))
                if not cat:
                    self._err("BR-26", "BT-151 : Le code catégorie TVA est obligatoire", loc)
                elif cat not in VALID_VAT_CATS:
                    self._err("BR-26",
                        f"BT-151 : Catégorie TVA invalide '{cat}' "
                        f"(valeurs : {', '.join(sorted(VALID_VAT_CATS))})", loc)
                if cat in ZERO_VAT_CATS and rate != Decimal("0"):
                    self._err(f"BR-{cat}-05",
                        f"BT-152 : Taux TVA doit être 0% pour catégorie {cat} (reçu {rate}%)", loc)

        # BG-29 — Prix
        price = line.find(f"{cac}Price")
        if price is None:
            self._err("BR-27", "BG-29 : Le bloc Prix est obligatoire", loc)
        else:
            pa = price.find(f"{cbc}PriceAmount")
            if pa is None or not (pa.text or "").strip():
                self._err("BR-27", "BT-146 : Le montant du prix unitaire est obligatoire", loc)

        # BR-CO-03 : BT-131 = BT-129 × (BT-146 / BT-149) − ∑remises + ∑frais
        if qty_el is not None and net_el is not None and price is not None:
            try:
                qty        = _d(qty_el.text)
                pa_el      = price.find(f"{cbc}PriceAmount")
                unit_price = _d(pa_el.text if pa_el is not None else "0")
                bq_el      = price.find(f"{cbc}BaseQuantity")
                base_qty   = _d(bq_el.text if bq_el is not None and (bq_el.text or "").strip() else "1")
                if base_qty == Decimal("0"):
                    base_qty = Decimal("1")

                # Somme de toutes les remises et frais de ligne
                total_allow = Decimal("0")
                total_charge = Decimal("0")
                for ac in line.findall(f"{cac}AllowanceCharge"):
                    ci = _txt(ac, f"{cbc}ChargeIndicator").lower()
                    amt = _d(_txt(ac, f"{cbc}Amount"))
                    if ci == "false":
                        total_allow += amt
                    else:
                        total_charge += amt

                net = _d(net_el.text)
                gross = (qty * unit_price / base_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Interprétation 1 : PriceAmount = prix net (remises déjà incluses, AllowanceCharge documentaire)
                expected_net_price = gross
                # Interprétation 2 : PriceAmount = prix brut, remises à déduire (formule EN16931 stricte)
                expected_gross_price = (gross - total_allow + total_charge).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                ok1 = abs(net - expected_net_price) <= Decimal("0.02")
                ok2 = abs(net - expected_gross_price) <= Decimal("0.02")

                if not ok1 and not ok2:
                    base_info = f" / Base ({base_qty})" if base_qty != Decimal("1") else ""
                    disc_info = f" − Remises ({total_allow})" if total_allow else ""
                    chrg_info = f" + Frais ({total_charge})" if total_charge else ""
                    self._warn("BR-CO-03",
                        f"BT-131 : Montant net ({net}) ne correspond ni à "
                        f"Qté ({qty}) × Prix ({unit_price}){base_info} = {expected_net_price} "
                        f"ni à {expected_net_price}{disc_info}{chrg_info} = {expected_gross_price}", loc)
            except Exception:
                pass

    # ── Validation CII D16B ──────────────────────────────────────────

    def _validate_cii(self):
        r   = self.root
        ram = self._ram
        rsm = self._rsm
        udt = self._udt

        # BR-01 — GuidelineID (BT-24)
        cid = _txt(r,
            f"{rsm}ExchangedDocumentContext/"
            f"{ram}GuidelineSpecifiedDocumentContextParameter/{ram}ID")
        if not cid:
            self._err("BR-01", "BT-24 : L'identifiant de spécification est obligatoire")
        elif "en16931" not in cid:
            self._warn("BR-01", f"BT-24 : GuidelineID '{cid}' ne référence pas en16931")

        doc = r.find(f"{rsm}ExchangedDocument")
        if doc is None:
            self._err("STRUCT-01", "ExchangedDocument est obligatoire")
            return

        # BR-02 — ID facture (BT-1)
        if not _txt(doc, f"{ram}ID"):
            self._err("BR-02", "BT-1 : Le numéro de facture est obligatoire")

        # BR-04 — Code type (BT-3)
        type_code = _txt(doc, f"{ram}TypeCode")
        self.type_code = type_code
        if not type_code:
            self._err("BR-04", "BT-3 : Le code de type est obligatoire")
        elif type_code not in VALID_TYPE_CODES:
            self._err("BR-04",
                f"BT-3 : Code type invalide '{type_code}' "
                f"(valeurs : {', '.join(sorted(VALID_TYPE_CODES))})")

        # BR-03 — Date d'émission (BT-2) au format AAAAMMJJ avec format="102"
        dt_el = doc.find(f"{ram}IssueDateTime/{udt}DateTimeString")
        if dt_el is None:
            self._err("BR-03", "BT-2 : La date d'émission est obligatoire")
        else:
            fmt_attr = dt_el.get("format", "")
            if fmt_attr != "102":
                self._err("BR-03",
                    f"BT-2 : Attribut format incorrect sur DateTimeString "
                    f"(attendu '102', reçu '{fmt_attr}')")
            date_val = (dt_el.text or "").strip()
            if not re.match(r"^\d{8}$", date_val):
                self._err("BR-03",
                    f"BT-2 : Date invalide '{date_val}' (attendu AAAAMMJJ, ex : 20260612)")

        sctt = r.find(f"{rsm}SupplyChainTradeTransaction")
        if sctt is None:
            self._err("STRUCT-02", "SupplyChainTradeTransaction est obligatoire")
            return

        hta = sctt.find(f"{ram}ApplicableHeaderTradeAgreement")
        hts = sctt.find(f"{ram}ApplicableHeaderTradeSettlement")

        if hta is None:
            self._err("STRUCT-03", "ApplicableHeaderTradeAgreement est obligatoire")
        else:
            self._validate_cii_parties(hta)

        if hts is None:
            self._err("STRUCT-04", "ApplicableHeaderTradeSettlement est obligatoire")
        else:
            self._validate_cii_settlement(sctt, hts)

        # BG-25 — Lignes
        lines = sctt.findall(f"{ram}IncludedSupplyChainTradeLineItem")
        if not lines:
            self._err("BR-16", "BG-25 : Au moins une ligne est obligatoire")
        else:
            cur = _txt(hts, f"{ram}InvoiceCurrencyCode") if hts is not None else "EUR"
            for i, line in enumerate(lines, 1):
                self._validate_cii_line(line, i)

        # Vérification des montants positifs pour les avoirs (BT-3 = 381)
        if self.type_code == "381" and hts is not None:
            self._check_credit_note_amounts_cii(sctt, hts)

    def _check_credit_note_amounts_cii(self, sctt, hts):
        ram = self._ram
        sms = hts.find(f"{ram}SpecifiedTradeSettlementHeaderMonetarySummation")

        if sms is not None:
            amount_checks = [
                (f"{ram}LineTotalAmount",    "BT-106", "LineTotalAmount"),
                (f"{ram}TaxBasisTotalAmount","BT-109", "TaxBasisTotalAmount"),
                (f"{ram}TaxTotalAmount",     "BT-110", "TaxTotalAmount"),
                (f"{ram}GrandTotalAmount",   "BT-112", "GrandTotalAmount"),
                (f"{ram}DuePayableAmount",   "BT-115", "DuePayableAmount"),
            ]
            for tag, bt, label in amount_checks:
                el = sms.find(tag)
                if el is not None and el.text:
                    val = _d(el.text)
                    if val < Decimal("0"):
                        self._err("BR-AV-01",
                            f"{bt} : Dans un avoir (381), {label} doit être positif ou nul (reçu : {val})")

        # Montants de lignes
        lines = sctt.findall(f"{ram}IncludedSupplyChainTradeLineItem")
        for i, line in enumerate(lines, 1):
            el = line.find(
                f"{ram}SpecifiedLineTradeSettlement/"
                f"{ram}SpecifiedTradeSettlementLineMonetarySummation/"
                f"{ram}LineTotalAmount")
            if el is not None and el.text:
                val = _d(el.text)
                if val < Decimal("0"):
                    self._err("BR-AV-01",
                        f"BT-131 : Dans un avoir (381), le montant net de ligne doit être positif ou nul "
                        f"(reçu : {val})", f"Ligne {i}")

    def _validate_cii_parties(self, hta):
        ram = self._ram

        seller = hta.find(f"{ram}SellerTradeParty")
        if seller is None:
            self._err("BR-06", "BG-4 : Le fournisseur (SellerTradeParty) est obligatoire")
        else:
            self._validate_cii_party(seller, "fournisseur", "BG-4")

        buyer = hta.find(f"{ram}BuyerTradeParty")
        if buyer is None:
            self._err("BR-07", "BG-7 : L'acheteur (BuyerTradeParty) est obligatoire")
        else:
            if not _txt(buyer, f"{ram}Name"):
                self._err("BR-07", "BT-44 : Le nom de l'acheteur est obligatoire")

        # BT-10 — BuyerReference
        if not _txt(hta, f"{ram}BuyerReference"):
            self._warn("BT-10",
                "BT-10 : La référence acheteur est recommandée (obligatoire pour Chorus Pro / Coupa)")

    def _validate_cii_party(self, party, label, bg):
        ram = self._ram

        # Nom
        if not _txt(party, f"{ram}Name"):
            self._err("BR-06" if label == "fournisseur" else "BR-07",
                f"BT-{'27' if label == 'fournisseur' else '44'} : Le nom du {label} est obligatoire")

        # Adresse
        addr = party.find(f"{ram}PostalTradeAddress")
        if addr is None:
            if label == "fournisseur":
                self._err("BR-11", f"{bg} : L'adresse postale du {label} est obligatoire")
        else:
            country = _txt(addr, f"{ram}CountryID")
            if not country:
                self._err("BR-12" if label == "fournisseur" else "BR-13",
                    f"BT-{'40' if label == 'fournisseur' else '55'} : Le code pays est obligatoire")
            elif country not in ISO_COUNTRIES:
                self._err("BR-12" if label == "fournisseur" else "BR-13",
                    f"BT-{'40' if label == 'fournisseur' else '55'} : Code pays invalide '{country}'")

            if label == "fournisseur":
                if not _txt(addr, f"{ram}LineOne"):
                    self._warn("BT-35", "BT-35 : La rue du fournisseur est recommandée")
                if not _txt(addr, f"{ram}CityName"):
                    self._warn("BT-37", "BT-37 : La ville du fournisseur est recommandée")
                if not _txt(addr, f"{ram}PostcodeCode"):
                    self._warn("BT-38", "BT-38 : Le code postal du fournisseur est recommandé")

        # TVA fournisseur
        vat_el = party.find(f"{ram}SpecifiedTaxRegistration/{ram}ID")
        if vat_el is not None and vat_el.text:
            vat = vat_el.text.strip()
            scheme = vat_el.get("schemeID", "")
            if scheme and scheme not in ("VA", "FC"):
                self._warn("BT-31",
                    f"BT-31 : SchemeID '{scheme}' inhabituel sur SpecifiedTaxRegistration/ID "
                    f"(attendu 'VA' pour TVA)")
            if not re.match(r"^[A-Z]{2}[0-9A-Z]{2,12}$", vat):
                self._warn("BT-31",
                    f"BT-31 : Format du numéro TVA {label} suspect : '{vat}' "
                    f"(attendu ex : FR07433927332)")

        # Identifiant légal (SIREN/SIRET)
        lo = party.find(f"{ram}SpecifiedLegalOrganization/{ram}ID")
        if label == "fournisseur" and (lo is None or not (lo.text or "").strip()):
            self._warn("BT-30", "BT-30 : L'identifiant légal du fournisseur (SIREN) est recommandé")

    def _validate_cii_settlement(self, sctt, hts):
        ram = self._ram
        udt = self._udt

        # BR-05 — Code monnaie (BT-5)
        currency = _txt(hts, f"{ram}InvoiceCurrencyCode")
        if not currency:
            self._err("BR-05", "BT-5 : Le code monnaie (InvoiceCurrencyCode) est obligatoire")
        elif not re.match(r"^[A-Z]{3}$", currency):
            self._err("BR-05", f"BT-5 : Code monnaie invalide '{currency}'")

        # BG-23 — TVA
        tax_els = hts.findall(f"{ram}ApplicableTradeTax")
        if not tax_els:
            self._err("BR-45", "BG-23 : Au moins une entrée ApplicableTradeTax est obligatoire")
        else:
            for i, tax in enumerate(tax_els, 1):
                cat  = _txt(tax, f"{ram}CategoryCode")
                rate = _d(_txt(tax, f"{ram}RateApplicablePercent"))
                taxable = _d(_txt(tax, f"{ram}BasisAmount"))
                tax_amt = _d(_txt(tax, f"{ram}CalculatedAmount"))
                tc_type = _txt(tax, f"{ram}TypeCode")

                if tc_type and tc_type != "VAT":
                    self._warn("BT-118",
                        f"BG-23 / TVA {i} : TypeCode '{tc_type}' inattendu (attendu 'VAT')")

                if not cat:
                    self._err("BR-46",
                        f"BG-23 / TVA {i} : Le code catégorie est obligatoire")
                elif cat not in VALID_VAT_CATS:
                    self._err("BR-46",
                        f"BG-23 / TVA {i} : Catégorie invalide '{cat}' "
                        f"(valeurs : {', '.join(sorted(VALID_VAT_CATS))})")

                if cat in ZERO_VAT_CATS and rate != Decimal("0"):
                    self._err(f"BR-{cat}-08",
                        f"BG-23 / TVA {i} : Taux 0% obligatoire pour catégorie {cat} (reçu {rate}%)")

                if rate > Decimal("0") and taxable > Decimal("0"):
                    expected = (taxable * rate / Decimal("100")).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if abs(tax_amt - expected) > Decimal("0.02"):
                        self._warn("BR-CO-17",
                            f"BG-23 / TVA {i} : Montant TVA déclaré ({tax_amt}) ≠ "
                            f"base imposable ({taxable}) × taux ({rate}%) / 100 = {expected}")

        # Vérification de TaxTotalAmount avec currencyID (EN16931 / Factur-X)
        sms = hts.find(f"{ram}SpecifiedTradeSettlementHeaderMonetarySummation")
        tta_el = sms.find(f"{ram}TaxTotalAmount") if sms is not None else None
        if tta_el is not None and not tta_el.get("currencyID"):
            self._warn("BT-110",
                "BT-110 : TaxTotalAmount devrait porter l'attribut currencyID (requis EN16931/Factur-X)")

        # BG-22 — Totaux
        if sms is None:
            self._err("BR-12",
                "BG-22 : SpecifiedTradeSettlementHeaderMonetarySummation est obligatoire")
        else:
            self._validate_cii_totals(sctt, hts, sms, currency)

        # BT-9 — Date d'échéance
        due_el = hts.find(
            f"{ram}SpecifiedTradePaymentTerms/{ram}DueDateDateTime/{udt}DateTimeString")
        if due_el is not None and due_el.text:
            due_val = due_el.text.strip()
            if not re.match(r"^\d{8}$", due_val):
                self._err("BR-03",
                    f"BT-9 : Date d'échéance invalide '{due_val}' (attendu AAAAMMJJ)")
            if due_el.get("format", "") != "102":
                self._err("BR-03", "BT-9 : Attribut format='102' obligatoire sur DueDateDateTime")

    def _validate_cii_totals(self, sctt, hts, sms, currency):
        ram = self._ram

        def g(tag):
            el = sms.find(f"{ram}{tag}")
            return _d(el.text if el is not None else "0")

        line_total    = g("LineTotalAmount")      # BT-106
        charge_total  = g("ChargeTotalAmount")    # BT-108
        allow_total   = g("AllowanceTotalAmount") # BT-107
        tax_basis     = g("TaxBasisTotalAmount")  # BT-109
        tax_total     = g("TaxTotalAmount")       # BT-110
        grand_total   = g("GrandTotalAmount")     # BT-112
        prepaid       = g("TotalPrepaidAmount")   # BT-113
        payable       = g("DuePayableAmount")     # BT-115

        # BR-CO-10 : BT-106 = somme LineTotalAmount lignes
        lines = sctt.findall(f"{ram}IncludedSupplyChainTradeLineItem")
        line_amts = [
            _d(_txt(l,
                f"{ram}SpecifiedLineTradeSettlement/"
                f"{ram}SpecifiedTradeSettlementLineMonetarySummation/"
                f"{ram}LineTotalAmount"))
            for l in lines
        ]
        sum_lines = sum(line_amts)
        if abs(line_total - sum_lines) > Decimal("0.02"):
            detail = " + ".join(str(a) for a in line_amts)
            self._err("BR-CO-10",
                f"BT-106 : LineTotalAmount ({line_total}) ≠ somme des lignes ({detail} = {sum_lines})")

        # BR-CO-11 : BT-109 = BT-106 − BT-107 + BT-108
        expected_basis = line_total - allow_total + charge_total
        if abs(tax_basis - expected_basis) > Decimal("0.02"):
            self._err("BR-CO-11",
                f"BT-109 : TaxBasisTotalAmount ({tax_basis}) ≠ "
                f"BT-106 ({line_total}) − BT-107 ({allow_total}) + BT-108 ({charge_total}) = {expected_basis}")

        # BR-CO-13 : BT-112 = BT-109 + BT-110
        expected_grand = tax_basis + tax_total
        if abs(grand_total - expected_grand) > Decimal("0.02"):
            self._err("BR-CO-13",
                f"BT-112 : GrandTotalAmount ({grand_total}) ≠ "
                f"BT-109 ({tax_basis}) + BT-110 ({tax_total}) = {expected_grand}")

        # BR-CO-16 : BT-115 = BT-112 − BT-113
        expected_payable = grand_total - prepaid
        if abs(payable - expected_payable) > Decimal("0.02"):
            self._err("BR-CO-16",
                f"BT-115 : DuePayableAmount ({payable}) ≠ "
                f"BT-112 ({grand_total}) − BT-113 ({prepaid}) = {expected_payable}")

    def _validate_cii_line(self, line, idx):
        ram = self._ram
        loc = f"Ligne {idx}"

        # BT-126 — LineID
        if not _txt(line, f"{ram}AssociatedDocumentLineDocument/{ram}LineID"):
            self._err("BR-21", "BT-126 : L'identifiant de ligne est obligatoire", loc)

        # BT-153 — Nom produit
        if not _txt(line, f"{ram}SpecifiedTradeProduct/{ram}Name"):
            self._err("BR-25", "BT-153 : Le nom de l'article est obligatoire", loc)

        # BT-129/130 — Quantité + unité
        qty_el = line.find(
            f"{ram}SpecifiedLineTradeDelivery/{ram}BilledQuantity")
        if qty_el is None:
            self._err("BR-22", "BT-129 : La quantité facturée est obligatoire", loc)
        else:
            unit = qty_el.get("unitCode", "")
            if not unit:
                self._err("BR-23", "BT-130 : Le code unité (unitCode) est obligatoire", loc)
            elif unit not in VALID_UNIT_CODES:
                self._warn("BR-23",
                    f"BT-130 : Code unité '{unit}' non reconnu par UN/ECE Rec 20/21 "
                    f"(ex : C62=pièce, KGM=kg, LTR=litre, HUR=heure, DAY=jour, MTR=mètre)", loc)

        # BT-146 — Prix unitaire
        price_el = line.find(
            f"{ram}SpecifiedLineTradeAgreement/"
            f"{ram}NetPriceProductTradePrice/{ram}ChargeAmount")
        if price_el is None:
            self._err("BR-27", "BT-146 : Le prix unitaire est obligatoire", loc)

        # BT-151/152 — Catégorie TVA
        tax_el = line.find(
            f"{ram}SpecifiedLineTradeSettlement/{ram}ApplicableTradeTax")
        if tax_el is None:
            self._err("BR-26", "BT-151 : La TVA de ligne est obligatoire", loc)
        else:
            cat  = _txt(tax_el, f"{ram}CategoryCode")
            rate = _d(_txt(tax_el, f"{ram}RateApplicablePercent"))
            if not cat:
                self._err("BR-26", "BT-151 : Le code catégorie TVA est obligatoire", loc)
            elif cat not in VALID_VAT_CATS:
                self._err("BR-26",
                    f"BT-151 : Catégorie TVA invalide '{cat}' "
                    f"(valeurs : {', '.join(sorted(VALID_VAT_CATS))})", loc)
            if cat in ZERO_VAT_CATS and rate != Decimal("0"):
                self._err(f"BR-{cat}-05",
                    f"BT-152 : Taux 0% obligatoire pour catégorie {cat} (reçu {rate}%)", loc)

        # BT-131 — Montant net ligne
        lt_el = line.find(
            f"{ram}SpecifiedLineTradeSettlement/"
            f"{ram}SpecifiedTradeSettlementLineMonetarySummation/"
            f"{ram}LineTotalAmount")
        if lt_el is None:
            self._err("BR-24", "BT-131 : Le montant net de ligne est obligatoire", loc)
        else:
            # BR-CO-03
            try:
                qty        = _d(qty_el.text if qty_el is not None else "0")
                unit_price = _d(price_el.text if price_el is not None else "0")
                bq_el      = line.find(
                    f"{ram}SpecifiedLineTradeAgreement/"
                    f"{ram}NetPriceProductTradePrice/{ram}BasisQuantity")
                base_qty   = _d(bq_el.text if bq_el is not None and (bq_el.text or "").strip() else "1")
                if base_qty == Decimal("0"):
                    base_qty = Decimal("1")

                # Somme de toutes les remises et frais de ligne
                total_allow = Decimal("0")
                total_charge = Decimal("0")
                for ac in line.findall(
                        f"{ram}SpecifiedLineTradeSettlement/"
                        f"{ram}SpecifiedTradeAllowanceCharge"):
                    ci = _txt(ac, f"{ram}ChargeIndicator").lower()
                    amt = _d(_txt(ac, f"{ram}ActualAmount"))
                    if ci == "false":
                        total_allow += amt
                    else:
                        total_charge += amt

                net   = _d(lt_el.text)
                gross = (qty * unit_price / base_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Interprétation 1 : prix net (remises déjà dans le prix)
                expected_net_price = gross
                # Interprétation 2 : prix brut, remises à déduire (formule EN16931 stricte)
                expected_gross_price = (gross - total_allow + total_charge).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                ok1 = abs(net - expected_net_price) <= Decimal("0.02")
                ok2 = abs(net - expected_gross_price) <= Decimal("0.02")

                if not ok1 and not ok2:
                    base_info = f" / Base ({base_qty})" if base_qty != Decimal("1") else ""
                    disc_info = f" − Remises ({total_allow})" if total_allow else ""
                    chrg_info = f" + Frais ({total_charge})" if total_charge else ""
                    self._warn("BR-CO-03",
                        f"BT-131 : Montant net ({net}) ne correspond ni à "
                        f"Qté ({qty}) × Prix ({unit_price}){base_info} = {expected_net_price} "
                        f"ni à {expected_net_price}{disc_info}{chrg_info} = {expected_gross_price}", loc)
            except Exception:
                pass


# ── Interface en ligne de commande ────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Validateur de factures électroniques EN16931/AFNOR (UBL 2.1 / CII D16B)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Règles vérifiées :
  Bien-formedness XML, détection du format (UBL/CII),
  champs obligatoires EN16931 (BR-01 à BR-27), formats de dates,
  codes pays/monnaie, catégories TVA, cohérence des montants (BR-CO-*).

  Validation XSD optionnelle via lxml :
    pip install lxml
    Placez UBL-Invoice-2.1.xsd et CrossIndustryInvoice_100pD16B.xsd dans --schemas

Exemples :
  python invoice_validator.py facture.xml
  python invoice_validator.py facture.xml --schemas ./schemas
  python invoice_validator.py facture.xml --format json
  python invoice_validator.py facture.xml --format json > rapport.json
        """
    )
    parser.add_argument("xml_file", help="Fichier XML à valider")
    parser.add_argument("--schemas", metavar="DIR",
                        help="Répertoire contenant les schémas XSD (optionnel)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Format de sortie (défaut : text)")
    args = parser.parse_args()

    if not Path(args.xml_file).exists():
        print(f"Erreur : fichier introuvable : {args.xml_file}", file=sys.stderr)
        sys.exit(1)

    validator = InvoiceValidator(args.xml_file, args.schemas)
    issues    = validator.validate()

    errors   = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]
    is_valid = len(errors) == 0

    if args.format == "json":
        result = {
            "file":     str(args.xml_file),
            "format":   validator.fmt,
            "valid":    is_valid,
            "errors":   [{"code": i.code, "message": i.message, "location": i.location}
                         for i in errors],
            "warnings": [{"code": i.code, "message": i.message, "location": i.location}
                         for i in warnings],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sep = "─" * 64
        print(f"\n{sep}")
        print(f"  Fichier   : {args.xml_file}")
        print(f"  Format    : {validator.fmt or 'inconnu'}")
        print(f"  Statut    : {'VALIDE' if is_valid else 'NON CONFORME'}")
        print(f"  Erreurs   : {len(errors)}   |   Avertissements : {len(warnings)}")
        print(sep)
        if issues:
            for iss in issues:
                print(f"  {iss}")
        else:
            print("  Aucune anomalie détectée.")
        print(f"{sep}\n")

    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
