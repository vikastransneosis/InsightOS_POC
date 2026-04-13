"""
Entity resolution: normalize co-references to canonical forms.
"HZL", "Hindustan Zinc", "Hindustan Zinc Limited" → "HINDZINC"
"""
from __future__ import annotations

ENTITY_ALIASES: dict[str, list[str]] = {
    "FEDERALBNK": [
        "Federal Bank", "Federal Bank Ltd", "Federal Bank Limited",
        "the Bank", "FB",
    ],
    "HINDZINC": [
        "Hindustan Zinc", "Hindustan Zinc Ltd", "Hindustan Zinc Limited",
        "HZL",
    ],
    "MANKIND": [
        "Mankind Pharma", "Mankind Pharma Ltd", "Mankind Pharma Limited",
        "Mankind",
    ],
    "CHOLAFIN": [
        "Cholamandalam", "Chola", "Cholamandalam Investment",
        "Cholamandalam Inv & Fin",
    ],
    "VEDANTA": [
        "Vedanta Resources", "Vedanta Ltd", "Vedanta Limited", "Vedanta",
    ],
    "RBI": [
        "Reserve Bank of India", "RBI",
    ],
    "SEBI": [
        "Securities and Exchange Board of India", "SEBI",
    ],
    "CRISIL": [
        "CRISIL", "CRISIL Ltd", "CRISIL Ratings",
    ],
    "ICRA": [
        "ICRA", "ICRA Ltd", "ICRA Limited",
    ],
}


def resolve_entity(raw_name: str, company_id: str = "") -> str:
    """Normalize entity name to canonical form."""
    raw_lower = raw_name.lower().strip()
    for canonical, aliases in ENTITY_ALIASES.items():
        for alias in aliases:
            if alias.lower() in raw_lower or raw_lower in alias.lower():
                return canonical
    return raw_name


def get_all_aliases() -> dict[str, list[str]]:
    return dict(ENTITY_ALIASES)
