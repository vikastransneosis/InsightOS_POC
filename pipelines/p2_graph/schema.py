"""
Strict financial entity and relationship schema for knowledge graph construction.
Constrains LLM extraction to well-defined entity types and relations.
"""
from typing import Literal

FINANCIAL_ENTITIES = Literal[
    "COMPANY",
    "SUBSIDIARY",
    "PROMOTER",
    "SHAREHOLDER",
    "OFFICER",
    "AUDITOR",
    "RATING_AGENCY",
    "BANK",
    "REGULATOR",
    "VENDOR_CUSTOMER",
]

FINANCIAL_RELATIONS = Literal[
    "SUBSIDIARY_OF",
    "PROMOTED_BY",
    "HOLDS_STAKE_IN",
    "OFFICER_OF",
    "AUDITED_BY",
    "RATED_BY",
    "RELATED_PARTY_OF",
    "BORROWS_FROM",
    "JV_PARTNER_OF",
    "REGULATED_BY",
    "SUPPLIES_TO",
]

FINANCIAL_SCHEMA = {
    "COMPANY": [
        "SUBSIDIARY_OF", "PROMOTED_BY", "AUDITED_BY",
        "RATED_BY", "RELATED_PARTY_OF", "BORROWS_FROM",
        "JV_PARTNER_OF", "REGULATED_BY",
    ],
    "SUBSIDIARY": ["SUBSIDIARY_OF", "JV_PARTNER_OF"],
    "PROMOTER": ["PROMOTED_BY", "HOLDS_STAKE_IN"],
    "SHAREHOLDER": ["HOLDS_STAKE_IN"],
    "OFFICER": ["OFFICER_OF"],
    "AUDITOR": ["AUDITED_BY"],
    "RATING_AGENCY": ["RATED_BY"],
    "BANK": ["BORROWS_FROM"],
    "VENDOR_CUSTOMER": ["SUPPLIES_TO", "RELATED_PARTY_OF"],
    "REGULATOR": ["REGULATED_BY"],
}

ENTITY_TYPE_DESCRIPTIONS = {
    "COMPANY": "A listed or unlisted company entity",
    "SUBSIDIARY": "A subsidiary or joint venture of a parent company",
    "PROMOTER": "A promoter or promoter group holding shares",
    "SHAREHOLDER": "A major institutional or non-promoter shareholder",
    "OFFICER": "Key management personnel: MD, CEO, CFO, Director",
    "AUDITOR": "Statutory or internal auditor firm",
    "RATING_AGENCY": "Credit rating agency (CRISIL, ICRA, CARE, etc.)",
    "BANK": "A lending bank or financial institution",
    "REGULATOR": "Regulatory body (SEBI, RBI, MCA)",
    "VENDOR_CUSTOMER": "Key supplier, vendor, or customer",
}
