"""
Central configuration: model strings, pricing, and shared constants.

Bump a model or price here once instead of hunting through pages and utils.
"""

import streamlit as st

# ── Models ────────────────────────────────────────────────────────────────────
EXTRACTION_MODEL = "claude-haiku-4-5-20251001"   # URL Ingestion pick extraction
CHAT_MODEL = "claude-sonnet-4-6"                  # Chat Q&A / lineup narration

EXTRACTION_MAX_TOKENS = 8192

# ── Pricing (USD per million tokens) ─────────────────────────────────────────
CHAT_PRICE_INPUT_PER_MTOK = 3.0    # Sonnet input
CHAT_PRICE_OUTPUT_PER_MTOK = 15.0  # Sonnet output

# ── Fuzzy-matching thresholds ─────────────────────────────────────────────────
FUZZY_AUTO_RESOLVE = 85   # >= this: auto-resolve fighter name silently
FUZZY_MIN_PROMPT = 50     # below this: treat as a brand-new fighter
FUZZY_FIGHT_MATCH = 85    # fight-level dedup threshold in get_or_create_fight

# ── DraftKings ────────────────────────────────────────────────────────────────
DK_DEFAULT_SALARY_CAP = 50_000
DK_LINEUP_SIZE = 6
DK_LINEUP_POOL_SIZE = 15  # top-N fighters considered by the exhaustive search

# ── Chat memory ───────────────────────────────────────────────────────────────
CHAT_HISTORY_TURNS = 6  # prior messages (user+assistant) sent for context


def get_anthropic_api_key() -> str | None:
    """Read the Anthropic API key from Streamlit secrets.

    Supports both the nested [anthropic] section and a flat ANTHROPIC_API_KEY.
    Returns None if absent — callers decide how to surface that. Never echoes
    the names of other secrets (this app may be publicly reachable).
    """
    try:
        if "anthropic" in st.secrets:
            return st.secrets["anthropic"]["api_key"]
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return None
