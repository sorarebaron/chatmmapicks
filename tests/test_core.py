"""
Tests for the pure logic in ChatMMAPicks.

Run with:  pip install pytest && python -m pytest tests/ -v

These cover the functions most likely to silently regress during refactors:
odds math, name normalization, the DK lineup optimizer (including the
no-opponents constraint), event-name extraction, and salary-cap parsing.

Streamlit secrets aren't available under pytest, so st.secrets access is
stubbed before importing app modules.
"""

import sys
import types
from unittest.mock import MagicMock

# ── Stub streamlit before importing app modules ───────────────────────────────
# utils.chat / utils.db import streamlit at module load; none of the functions
# under test here actually touch it at call time.
_st = types.ModuleType("streamlit")
_st.secrets = {}
_st.cache_resource = lambda f=None, **kw: (f if f else (lambda g: g))
_st.cache_data = lambda f=None, **kw: (f if f else (lambda g: g))
sys.modules.setdefault("streamlit", _st)

from utils.chat import (  # noqa: E402
    ChatMMABot,
    QueryOptimizer,
    _fmt_odds,
    _odds_favorite,
)
from utils.db import _name_variants, _normalize_name  # noqa: E402


# ── Odds helpers ──────────────────────────────────────────────────────────────

class TestOdds:
    def test_fmt_negative(self):
        assert _fmt_odds(-150) == "-150"

    def test_fmt_positive_gets_plus(self):
        assert _fmt_odds(130) == "+130"

    def test_fmt_none(self):
        assert _fmt_odds(None) == "N/A"

    def test_favorite_negative_beats_positive(self):
        assert _odds_favorite(-200, 170) == "a"
        assert _odds_favorite(170, -200) == "b"

    def test_favorite_both_negative(self):
        # -300 implies higher win probability than -110
        assert _odds_favorite(-300, -110) == "a"

    def test_favorite_unknown_when_missing(self):
        assert _odds_favorite(None, -150) is None
        assert _odds_favorite(-150, None) is None


# ── Name normalization ────────────────────────────────────────────────────────

class TestNames:
    def test_strips_diacritics(self):
        assert _normalize_name("Ailín Pérez") == "ailin perez"

    def test_lowercases_and_strips(self):
        assert _normalize_name("  Jon JONES ") == "jon jones"

    def test_two_word_variants_include_reversal(self):
        assert _name_variants("Wang Cong") == ["Wang Cong", "Cong Wang"]

    def test_three_word_names_not_reversed(self):
        assert _name_variants("Benoit Saint Denis") == ["Benoit Saint Denis"]


# ── DK lineup optimizer ───────────────────────────────────────────────────────

def _fighter(name, fight_id, salary, consensus=80.0, total=10, finishes=5, odds=-150):
    return {
        "fighter": name,
        "opponent": "opp",
        "fight_id": fight_id,
        "salary": salary,
        "pick_count": int(total * consensus / 100),
        "total_picks": total,
        "consensus_pct": consensus,
        "finish_picks": finishes,
        "method_counts": {},
        "win_odds": odds,
        "itd_odds": None,
    }


class TestLineup:
    def test_respects_salary_cap(self):
        fighters = [_fighter(f"F{i}", f"fight{i}", 8000) for i in range(8)]
        lineup = QueryOptimizer._build_optimal_lineup(fighters, 50_000)
        assert lineup is not None
        assert sum(f["salary"] for f in lineup) <= 50_000

    def test_returns_six_fighters(self):
        fighters = [_fighter(f"F{i}", f"fight{i}", 7000) for i in range(10)]
        lineup = QueryOptimizer._build_optimal_lineup(fighters, 50_000)
        assert lineup is not None and len(lineup) == 6

    def test_never_rosters_both_fighters_from_same_fight(self):
        """Regression test for the same-fight bug.

        Construct a card where the two best-scoring fighters are opponents:
        without the constraint, the optimizer would roster both.
        """
        fighters = [
            _fighter("Star A", "fight1", 8000, consensus=90, finishes=9, odds=-300),
            _fighter("Star B", "fight1", 7000, consensus=10, finishes=1, odds=240),
            *[_fighter(f"F{i}", f"fight{i}", 7500, consensus=60) for i in range(2, 12)],
        ]
        lineup = QueryOptimizer._build_optimal_lineup(fighters, 50_000)
        assert lineup is not None
        fight_ids = [f["fight_id"] for f in lineup]
        assert len(fight_ids) == len(set(fight_ids)), "lineup contains opponents"

    def test_none_when_cap_impossible(self):
        fighters = [_fighter(f"F{i}", f"fight{i}", 10_000) for i in range(8)]
        assert QueryOptimizer._build_optimal_lineup(fighters, 30_000) is None


# ── Query routing / extraction (no DB or API needed for these paths) ─────────

def _bot():
    """ChatMMABot without a real Anthropic client."""
    bot = ChatMMABot.__new__(ChatMMABot)
    bot.client = MagicMock()
    bot.model = "test-model"
    bot.optimizer = QueryOptimizer()
    bot.generator = MagicMock()
    return bot


class TestRouting:
    def test_detects_draftkings(self):
        # _extract_event_name needs a DB for fallback; patch it out.
        bot = _bot()
        bot._extract_event_name = lambda q: "UFC 320"
        qtype, details = bot.detect_query_type("optimal dk lineup for UFC 320 under $50,000")
        assert qtype == "draftkings_lineup"
        assert details["salary_cap"] == 50_000

    def test_detects_fight_specific_vs(self):
        bot = _bot()
        bot._extract_event_name = lambda q: None
        qtype, details = bot.detect_query_type("who wins Jon Jones vs Tom Aspinall?")
        assert qtype == "fight_specific"
        assert details["fighter_a"] == "Jon Jones"
        assert details["fighter_b"] == "Tom Aspinall"

    def test_detects_underdogs(self):
        bot = _bot()
        bot._extract_event_name = lambda q: "UFC 320"
        qtype, _ = bot.detect_query_type("best underdog picks for UFC 320?")
        assert qtype == "underdogs"


class TestSalaryCap:
    def test_parses_with_commas(self):
        assert _bot()._extract_salary_cap("lineup under $45,500 please") == 45_500

    def test_defaults_to_50k(self):
        assert _bot()._extract_salary_cap("optimal dk lineup") == 50_000

    def test_rejects_absurd_values(self):
        assert _bot()._extract_salary_cap("lineup under $5") == 50_000
