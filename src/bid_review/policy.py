"""Shared procurement-review policy used by CrewAI prompts and deterministic services.

Values live in config/policy.yaml; this module exposes them under stable names.
"""
from __future__ import annotations

from string import Template

from .settings import load_config

_POLICY = load_config("policy")

TARGET_LEGAL_ENTITY: str = _POLICY["target_legal_entity"]
VISIBLE_COMPANY_NAME: str = _POLICY["visible_company_name"]
G2B_COMPANY_SEARCH_TERMS: tuple[str, ...] = tuple(_POLICY["g2b_company_search_terms"])
# Code-coupled artifact paths (models.py validates against these); not a config value.
SOURCE_ARTIFACTS = ("debug/01_rfp_analysis.json", "debug/02_capability_matching.json", "debug/03_market_research.json")

COMMON_AGENT_RULES: str = Template(_POLICY["common_agent_rules"]).substitute(target_legal_entity=TARGET_LEGAL_ENTITY).strip()
SAMPLE_EVIDENCE_NOTICE: str = _POLICY["sample_evidence_notice"]

PROMPT_VARIABLES = {
    "common_agent_rules": COMMON_AGENT_RULES,
    "target_legal_entity": TARGET_LEGAL_ENTITY,
    "g2b_search_terms": ", ".join(G2B_COMPANY_SEARCH_TERMS),
}


def render_prompt(text: str) -> str:
    """Fill `$name` placeholders from policy; CrewAI `{inputs}` stay untouched. Unknown names raise KeyError."""
    return Template(text).substitute(PROMPT_VARIABLES)
