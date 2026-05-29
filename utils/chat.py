"""
ChatMMAPicks – chat backend.

Improvements over v1:
- Odds (win + ITD) included in fight context and prompts; favorites/underdogs
  identified from betting lines rather than analyst pick counts alone.
- Two-pass fighter lookup: fast ILIKE path first, then fuzzy fallback so
  misspelled names still resolve correctly.
- Actual fight results fetched from the results table and surfaced in answers.
- Underdog detection uses betting odds when available; analyst reasoning
  increased from 3 → 5 rationales per fighter for richer context.
"""

import re
from collections import Counter

import streamlit as st
from anthropic import Anthropic
from rapidfuzz import fuzz

from utils.db import get_supabase


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _fmt_odds(odds: int | None) -> str:
    """Format American odds: -150 → '-150', 130 → '+130'."""
    if odds is None:
        return "N/A"
    return f"+{odds}" if odds > 0 else str(odds)


def _odds_favorite(odds_a: int | None, odds_b: int | None) -> str | None:
    """Return 'a' if fighter_a is the betting favorite, 'b' if fighter_b, None if unknown."""
    if odds_a is None or odds_b is None:
        return None

    def implied_prob(o: int) -> float:
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)

    return "a" if implied_prob(odds_a) > implied_prob(odds_b) else "b"


# Shared SELECT strings
_FIGHT_SELECT = (
    "fight_id, fighter_a, fighter_b, weight_class, bout_order, "
    "fighter_a_win_odds, fighter_b_win_odds, fighter_a_itd_odds, fighter_b_itd_odds"
)
_FIGHT_SELECT_WITH_EVENT = (
    "fight_id, fighter_a, fighter_b, weight_class, bout_order, "
    "fighter_a_win_odds, fighter_b_win_odds, fighter_a_itd_odds, fighter_b_itd_odds, "
    "events(event_id, name, date, location)"
)


# ---------------------------------------------------------------------------
# QueryOptimizer
# ---------------------------------------------------------------------------

class QueryOptimizer:
    """Queries Supabase to build context dicts that feed PromptGenerator."""

    # ── internal helpers ────────────────────────────────────────────────────

    def _get_event(self, event_name: str) -> dict | None:
        db = get_supabase()
        resp = (
            db.table("events")
            .select("event_id, name, date, location")
            .ilike("name", f"%{event_name}%")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def _get_fights_for_event(self, event_id: str) -> list[dict]:
        db = get_supabase()
        resp = (
            db.table("fights")
            .select(_FIGHT_SELECT)
            .eq("event_id", event_id)
            .execute()
        )
        return resp.data or []

    def _get_picks_for_fight(self, fight_id: str) -> list[dict]:
        db = get_supabase()
        resp = (
            db.table("analyst_picks")
            .select(
                "pick_id, analyst_name, platform, picked_fighter, "
                "method_prediction, reasoning_notes"
            )
            .eq("fight_id", fight_id)
            .execute()
        )
        return resp.data or []

    def _get_result_for_fight(self, fight_id: str) -> dict | None:
        """Fetch the recorded result for a fight, or None if not yet entered."""
        db = get_supabase()
        resp = (
            db.table("results")
            .select("winner, method, round, time")
            .eq("fight_id", fight_id)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def _classify_picks(
        self, picks: list[dict], fighter_a: str, fighter_b: str
    ) -> tuple[list[dict], list[dict]]:
        picks_a, picks_b = [], []
        for p in picks:
            picked = p.get("picked_fighter") or ""
            score_a = fuzz.token_set_ratio(picked.lower(), fighter_a.lower())
            score_b = fuzz.token_set_ratio(picked.lower(), fighter_b.lower())
            if score_a >= score_b and score_a >= 60:
                picks_a.append(p)
            elif score_b > score_a and score_b >= 60:
                picks_b.append(p)
        return picks_a, picks_b

    def _build_fighter_context(self, picks: list[dict]) -> dict:
        methods = Counter(
            p["method_prediction"] for p in picks if p.get("method_prediction")
        )
        rationales = [p["reasoning_notes"] for p in picks if p.get("reasoning_notes")][:5]
        return {
            "methods": dict(methods),
            "example_rationales": rationales,
        }

    def _format_fight_row(self, row: dict) -> dict:
        ev = row.get("events") or {}
        return {
            "fight_id": row["fight_id"],
            "fighter_a": row["fighter_a"],
            "fighter_b": row["fighter_b"],
            "event": ev.get("name", "Unknown Event"),
            "date": ev.get("date"),
            "fighter_a_win_odds": row.get("fighter_a_win_odds"),
            "fighter_b_win_odds": row.get("fighter_b_win_odds"),
            "fighter_a_itd_odds": row.get("fighter_a_itd_odds"),
            "fighter_b_itd_odds": row.get("fighter_b_itd_odds"),
        }

    def _pick_best_row(
        self,
        rows: list[dict],
        fa_hint: str,
        fb_hint: str,
        event_name: str | None,
    ) -> dict | None:
        """Score candidate rows against both name hints; return the best match."""
        if not rows:
            return None

        def score(row: dict) -> float:
            fa, fb = row["fighter_a"], row["fighter_b"]
            s1 = (
                fuzz.token_set_ratio(fa_hint.lower(), fa.lower())
                + fuzz.token_set_ratio(fb_hint.lower(), fb.lower())
            ) / 2
            s2 = (
                fuzz.token_set_ratio(fa_hint.lower(), fb.lower())
                + fuzz.token_set_ratio(fb_hint.lower(), fa.lower())
            ) / 2
            base = max(s1, s2)
            if event_name:
                ev = row.get("events") or {}
                if event_name.lower() in (ev.get("name") or "").lower():
                    base += 20
            return base

        best = max(rows, key=score)
        return self._format_fight_row(best) if score(best) >= 45 else None

    # ── public API ───────────────────────────────────────────────────────────

    def get_fight_by_fighters(
        self, fighter_a_hint: str, fighter_b_hint: str, event_name: str | None = None
    ) -> dict | None:
        """
        Find a fight by two partial/fuzzy fighter names.

        Pass 1: ILIKE on both names simultaneously (fast).
        Pass 2: ILIKE on one name at a time, then fuzzy-score the other —
                handles misspellings that would break the combined query.
        """
        db = get_supabase()

        # Pass 1 — both names must substring-match
        for fa_h, fb_h in [
            (fighter_a_hint, fighter_b_hint),
            (fighter_b_hint, fighter_a_hint),
        ]:
            for col_a, col_b in [("fighter_a", "fighter_b"), ("fighter_b", "fighter_a")]:
                try:
                    resp = (
                        db.table("fights")
                        .select(_FIGHT_SELECT_WITH_EVENT)
                        .ilike(col_a, f"%{fa_h}%")
                        .ilike(col_b, f"%{fb_h}%")
                        .order("events(date)", desc=True)
                        .limit(5)
                        .execute()
                    )
                    if resp.data:
                        result = self._pick_best_row(
                            resp.data, fighter_a_hint, fighter_b_hint, event_name
                        )
                        if result:
                            return result
                except Exception:
                    pass

        # Pass 2 — search by one name, fuzzy-match the other
        candidates: list[dict] = []
        seen: set[str] = set()
        for hint in [fighter_a_hint, fighter_b_hint]:
            for col in ["fighter_a", "fighter_b"]:
                try:
                    resp = (
                        db.table("fights")
                        .select(_FIGHT_SELECT_WITH_EVENT)
                        .ilike(col, f"%{hint}%")
                        .order("events(date)", desc=True)
                        .limit(20)
                        .execute()
                    )
                    for row in resp.data or []:
                        if row["fight_id"] not in seen:
                            seen.add(row["fight_id"])
                            candidates.append(row)
                except Exception:
                    pass

        return self._pick_best_row(candidates, fighter_a_hint, fighter_b_hint, event_name)

    def aggregate_fight_context(
        self, fight_id: str, fight_meta: dict | None = None
    ) -> dict | None:
        """
        Aggregate all picks, odds, and recorded result for a fight.
        fight_meta: pre-fetched dict (avoids an extra DB call when already known).
        """
        if fight_meta is None:
            db = get_supabase()
            resp = (
                db.table("fights")
                .select(_FIGHT_SELECT + ", events(name, date)")
                .eq("fight_id", fight_id)
                .limit(1)
                .execute()
            )
            if not resp.data:
                return None
            row = resp.data[0]
            ev = row.get("events") or {}
            fight_meta = {
                "fight_id": fight_id,
                "fighter_a": row["fighter_a"],
                "fighter_b": row["fighter_b"],
                "event": ev.get("name", "Unknown Event"),
                "date": ev.get("date"),
                "fighter_a_win_odds": row.get("fighter_a_win_odds"),
                "fighter_b_win_odds": row.get("fighter_b_win_odds"),
                "fighter_a_itd_odds": row.get("fighter_a_itd_odds"),
                "fighter_b_itd_odds": row.get("fighter_b_itd_odds"),
            }

        picks = self._get_picks_for_fight(fight_id)
        result = self._get_result_for_fight(fight_id)

        fa = fight_meta["fighter_a"]
        fb = fight_meta["fighter_b"]
        picks_a, picks_b = self._classify_picks(picks, fa, fb)

        return {
            "fight": {
                "fighter_a": fa,
                "fighter_b": fb,
                "event": fight_meta["event"],
                "fighter_a_win_odds": fight_meta.get("fighter_a_win_odds"),
                "fighter_b_win_odds": fight_meta.get("fighter_b_win_odds"),
                "fighter_a_itd_odds": fight_meta.get("fighter_a_itd_odds"),
                "fighter_b_itd_odds": fight_meta.get("fighter_b_itd_odds"),
            },
            "summary": {
                "total_predictions": len(picks),
                "picks_for_a": len(picks_a),
                "picks_for_b": len(picks_b),
            },
            "fighter_a_context": self._build_fighter_context(picks_a),
            "fighter_b_context": self._build_fighter_context(picks_b),
            "analyst_info": {
                "top_analysts_a": list({p["analyst_name"] for p in picks_a})[:5],
                "top_analysts_b": list({p["analyst_name"] for p in picks_b})[:5],
            },
            "result": result,  # None if fight result not yet recorded
        }

    def get_event_consensus_picks(self, event_name: str) -> dict | None:
        event = self._get_event(event_name)
        if not event:
            return None

        fights = self._get_fights_for_event(event["event_id"])
        consensus_picks = []

        for fight in fights:
            picks = self._get_picks_for_fight(fight["fight_id"])
            if not picks:
                continue

            picks_a, picks_b = self._classify_picks(
                picks, fight["fighter_a"], fight["fighter_b"]
            )
            total = len(picks_a) + len(picks_b)
            if total == 0:
                continue

            consensus_count = max(len(picks_a), len(picks_b))
            consensus_fighter = (
                fight["fighter_a"] if len(picks_a) >= len(picks_b) else fight["fighter_b"]
            )
            opposing_count = min(len(picks_a), len(picks_b))

            a_odds = fight.get("fighter_a_win_odds")
            b_odds = fight.get("fighter_b_win_odds")
            fav_side = _odds_favorite(a_odds, b_odds)
            consensus_is_favorite = None
            if fav_side is not None:
                fav_fighter = fight["fighter_a"] if fav_side == "a" else fight["fighter_b"]
                consensus_is_favorite = consensus_fighter == fav_fighter

            consensus_picks.append({
                "fight": f"{fight['fighter_a']} vs {fight['fighter_b']}",
                "fighter_a": fight["fighter_a"],
                "fighter_b": fight["fighter_b"],
                "consensus_fighter": consensus_fighter,
                "consensus_count": consensus_count,
                "opposing_count": opposing_count,
                "total_predictions": total,
                "consensus_percentage": (consensus_count / total) * 100,
                "consensus_fighter_odds": (
                    a_odds if consensus_fighter == fight["fighter_a"] else b_odds
                ),
                "consensus_is_favorite": consensus_is_favorite,
            })

        consensus_picks.sort(key=lambda x: x["consensus_percentage"], reverse=True)
        return {"event": event["name"], "consensus_picks": consensus_picks}

    def get_inside_distance_picks(self, event_name: str) -> dict | None:
        event = self._get_event(event_name)
        if not event:
            return None

        fights = self._get_fights_for_event(event["event_id"])
        inside_distance_fights = []
        finish_methods = {"KO/TKO", "Submission", "KO", "TKO", "Sub"}

        for fight in fights:
            picks = self._get_picks_for_fight(fight["fight_id"])
            finish_picks = [
                p for p in picks if p.get("method_prediction") in finish_methods
            ]
            if len(finish_picks) < 3:
                continue

            picks_a, picks_b = self._classify_picks(
                finish_picks, fight["fighter_a"], fight["fighter_b"]
            )
            fa_count, fb_count = len(picks_a), len(picks_b)
            if fa_count == 0 and fb_count == 0:
                continue

            favored = fight["fighter_a"] if fa_count >= fb_count else fight["fighter_b"]
            finish_count = max(fa_count, fb_count)
            method_picks = picks_a if favored == fight["fighter_a"] else picks_b
            methods = [
                {"method": p["method_prediction"]}
                for p in method_picks
                if p.get("method_prediction")
            ]
            favored_itd_odds = (
                fight.get("fighter_a_itd_odds")
                if favored == fight["fighter_a"]
                else fight.get("fighter_b_itd_odds")
            )

            inside_distance_fights.append({
                "fight": f"{fight['fighter_a']} vs {fight['fighter_b']}",
                "fighter_a": fight["fighter_a"],
                "fighter_b": fight["fighter_b"],
                "favored_fighter": favored,
                "finish_prediction_count": finish_count,
                "methods": methods,
                "total_finish_predictions": len(finish_picks),
                "favored_itd_odds": favored_itd_odds,
            })

        inside_distance_fights.sort(
            key=lambda x: x["finish_prediction_count"], reverse=True
        )
        return {"event": event["name"], "inside_distance_picks": inside_distance_fights}

    def get_event_underdogs(self, event_name: str) -> dict | None:
        event = self._get_event(event_name)
        if not event:
            return None

        fights = self._get_fights_for_event(event["event_id"])
        underdog_picks = []

        for fight in fights:
            picks = self._get_picks_for_fight(fight["fight_id"])
            picks_a, picks_b = self._classify_picks(
                picks, fight["fighter_a"], fight["fighter_b"]
            )
            total = len(picks_a) + len(picks_b)
            if total < 3:
                continue

            a_odds = fight.get("fighter_a_win_odds")
            b_odds = fight.get("fighter_b_win_odds")
            fav_side = _odds_favorite(a_odds, b_odds)

            if fav_side is not None:
                # Use betting odds to determine the true underdog
                underdog_is_a = fav_side == "b"
            else:
                # Fall back to pick-count heuristic
                if total < 5:
                    continue
                underdog_is_a = len(picks_a) < len(picks_b)

            underdog_fighter = fight["fighter_a"] if underdog_is_a else fight["fighter_b"]
            underdog_picks_list = picks_a if underdog_is_a else picks_b
            favorite_picks_list = picks_b if underdog_is_a else picks_a
            underdog_count = len(underdog_picks_list)
            favorite_count = len(favorite_picks_list)

            if underdog_count == 0:
                continue
            # When no odds: skip if underdog has majority of picks (not really an underdog)
            if fav_side is None and underdog_count >= total / 2:
                continue

            underdog_picks.append({
                "fight": f"{fight['fighter_a']} vs {fight['fighter_b']}",
                "fighter_a": fight["fighter_a"],
                "fighter_b": fight["fighter_b"],
                "underdog": underdog_fighter,
                "underdog_count": underdog_count,
                "favorite_count": favorite_count,
                "total_predictions": total,
                "underdog_percentage": (underdog_count / total) * 100,
                "underdog_odds": a_odds if underdog_is_a else b_odds,
                "favorite_odds": b_odds if underdog_is_a else a_odds,
                "analysts_backing_underdog": [
                    {"name": p["analyst_name"], "reasoning": p.get("reasoning_notes")}
                    for p in underdog_picks_list
                ],
                "value_score": underdog_count / total,
            })

        underdog_picks.sort(key=lambda x: x["value_score"], reverse=True)
        return {"event": event["name"], "underdog_picks": underdog_picks}


# ---------------------------------------------------------------------------
# PromptGenerator
# ---------------------------------------------------------------------------

class PromptGenerator:
    """Builds lean, focused prompts for each query type."""

    @staticmethod
    def build_fight_analysis_prompt(context: dict, user_question: str) -> str:
        fight = context["fight"]
        summary = context["summary"]
        a_ctx = context["fighter_a_context"]
        b_ctx = context["fighter_b_context"]
        analyst_info = context.get("analyst_info", {})
        result = context.get("result")

        fa = fight["fighter_a"]
        fb = fight["fighter_b"]
        a_win = fight.get("fighter_a_win_odds")
        b_win = fight.get("fighter_b_win_odds")
        a_itd = fight.get("fighter_a_itd_odds")
        b_itd = fight.get("fighter_b_itd_odds")
        fav_side = _odds_favorite(a_win, b_win)
        betting_favorite = (fa if fav_side == "a" else fb) if fav_side else None

        prompt = f"""You are ChatMMAPicks, an AI that synthesizes MMA analyst predictions.

USER QUESTION: {user_question}

FIGHT CONTEXT:
Event: {fight['event']}
Fight: {fa} vs {fb}
"""

        # Actual result if recorded
        if result:
            prompt += f"\nACTUAL RESULT: {result['winner']} won by {result['method']}"
            if result.get("round"):
                prompt += f" in Round {result['round']}"
            if result.get("time"):
                prompt += f" at {result['time']}"
            prompt += "\n"

        # Betting odds
        has_win_odds = a_win is not None or b_win is not None
        has_itd_odds = a_itd is not None or b_itd is not None
        if has_win_odds or has_itd_odds:
            prompt += "\nBETTING ODDS (American format):\n"
            if has_win_odds:
                prompt += f"  Win:  {fa} {_fmt_odds(a_win)}  |  {fb} {_fmt_odds(b_win)}\n"
            if has_itd_odds:
                prompt += f"  ITD:  {fa} {_fmt_odds(a_itd)}  |  {fb} {_fmt_odds(b_itd)}\n"
            if betting_favorite:
                prompt += f"  Betting favorite: {betting_favorite}\n"

        # Analyst pick summary
        prompt += f"""
ANALYST PREDICTIONS:
- Total analysts: {summary['total_predictions']}
- Picking {fa}: {summary['picks_for_a']} analysts
- Picking {fb}: {summary['picks_for_b']} analysts
"""

        for fighter, ctx in [(fa, a_ctx), (fb, b_ctx)]:
            if ctx["methods"]:
                methods_str = ", ".join(f"{m} ({c})" for m, c in ctx["methods"].items())
                prompt += f"  Expected methods for {fighter}: {methods_str}\n"
            if ctx["example_rationales"]:
                prompt += f"\n  Analyst reasoning for {fighter}:\n"
                for i, note in enumerate(ctx["example_rationales"][:3], 1):
                    prompt += f"  {i}. {note[:250]}\n"

        top_a = ", ".join(analyst_info.get("top_analysts_a", [])[:5]) or "none"
        top_b = ", ".join(analyst_info.get("top_analysts_b", [])[:5]) or "none"
        prompt += f"\nTOP ANALYSTS:\n  For {fa}: {top_a}\n  For {fb}: {top_b}\n"

        prompt += """
INSTRUCTIONS:
1. Answer the user's question based on the context above.
2. Use betting odds to accurately label favorites and underdogs — never rely solely on pick counts for this.
3. If the fight has an actual result recorded, lead with that fact before discussing predictions.
4. Focus on WHY analysts favor each fighter, referencing specific reasoning notes.
5. If asked about methods, reference the expected finish types and ITD odds.
6. Keep response conversational and insightful (2-4 paragraphs).

RESPONSE:
"""
        return prompt

    @staticmethod
    def build_inside_distance_prompt(context: dict, user_question: str) -> str:
        prompt = f"""You are ChatMMAPicks, an AI that synthesizes MMA analyst predictions.

USER QUESTION: {user_question}

EVENT: {context['event']}

FIGHTERS MOST LIKELY TO WIN INSIDE THE DISTANCE (KO/TKO/Submission):
"""
        if not context["inside_distance_picks"]:
            prompt += "\nNo fighters have significant finish predictions for this event.\n"
        else:
            for idx, pick in enumerate(context["inside_distance_picks"][:10], 1):
                method_counts: dict = {}
                for m in pick["methods"]:
                    method_counts[m["method"]] = method_counts.get(m["method"], 0) + 1
                itd_str = (
                    f" | ITD odds: {_fmt_odds(pick['favored_itd_odds'])}"
                    if pick.get("favored_itd_odds") is not None
                    else ""
                )
                prompt += (
                    f"\n{idx}. {pick['favored_fighter']} ({pick['fight']})\n"
                    f"   - {pick['finish_prediction_count']} analysts predict a finish{itd_str}\n"
                    f"   - Methods: {', '.join(f'{m} ({c})' for m, c in method_counts.items())}\n"
                )

        prompt += """
INSTRUCTIONS:
1. Answer the user's question about which fighters are most likely to win inside the distance.
2. Highlight fighters with the most finish predictions and mention the expected method.
3. Reference ITD odds where available to add context on market expectations.
4. Keep response conversational and actionable (2-3 paragraphs).

RESPONSE:
"""
        return prompt

    @staticmethod
    def build_consensus_picks_prompt(context: dict, user_question: str) -> str:
        prompt = f"""You are ChatMMAPicks, an AI that synthesizes MMA analyst predictions.

USER QUESTION: {user_question}

EVENT: {context['event']}

CONSENSUS PICKS (sorted by pick strength):
"""
        for idx, pick in enumerate(context["consensus_picks"], 1):
            other = (
                pick["fighter_a"]
                if pick["consensus_fighter"] == pick["fighter_b"]
                else pick["fighter_b"]
            )
            odds_str = (
                f" | odds: {_fmt_odds(pick.get('consensus_fighter_odds'))}"
                if pick.get("consensus_fighter_odds") is not None
                else ""
            )
            fav_note = ""
            if pick.get("consensus_is_favorite") is False:
                fav_note = " ⚡ UNDERDOG CONSENSUS"
            elif pick.get("consensus_is_favorite") is True:
                fav_note = " (betting favorite)"

            prompt += (
                f"\n{idx}. {pick['consensus_fighter']} over {other}{fav_note}\n"
                f"   - {pick['consensus_count']}-{pick['opposing_count']} "
                f"({pick['consensus_percentage']:.0f}%){odds_str}\n"
            )

        prompt += """
INSTRUCTIONS:
1. Answer the user's question about consensus picks for this event.
2. Flag any fights where analysts are backing the betting underdog — these are especially interesting.
3. Highlight the strongest consensus picks and any notable contrarian fights.
4. Keep response conversational and actionable (2-3 paragraphs).

RESPONSE:
"""
        return prompt

    @staticmethod
    def build_underdogs_prompt(context: dict, user_question: str) -> str:
        prompt = f"""You are ChatMMAPicks, an AI that synthesizes MMA analyst predictions.

USER QUESTION: {user_question}

EVENT: {context['event']}

ANALYST-BACKED UNDERDOG PICKS:
"""
        if not context["underdog_picks"]:
            prompt += "\nNo underdog opportunities identified for this event.\n"
        else:
            for idx, pick in enumerate(context["underdog_picks"][:8], 1):
                odds_str = (
                    f" | odds: {_fmt_odds(pick['underdog_odds'])}"
                    if pick.get("underdog_odds") is not None
                    else ""
                )
                fav_odds_str = (
                    f" (favorite: {_fmt_odds(pick['favorite_odds'])})"
                    if pick.get("favorite_odds") is not None
                    else ""
                )
                prompt += (
                    f"\n{idx}. {pick['underdog']} ({pick['fight']})\n"
                    f"   - {pick['underdog_count']} of {pick['total_predictions']} analysts "
                    f"({pick['underdog_percentage']:.0f}%){odds_str}{fav_odds_str}\n"
                )
                if pick.get("analysts_backing_underdog"):
                    names = [a["name"] for a in pick["analysts_backing_underdog"][:3]]
                    prompt += f"   - Backed by: {', '.join(names)}\n"

        prompt += """
INSTRUCTIONS:
1. Answer the user's question about underdog picks.
2. Use the betting odds to frame the value — explain what the line implies vs. what analysts believe.
3. Explain why analysts back these underdogs despite market disagreement.
4. Keep response conversational and actionable (2-3 paragraphs).

RESPONSE:
"""
        return prompt

    @staticmethod
    def build_general_prompt(user_question: str) -> str:
        return f"""You are ChatMMAPicks, an AI assistant for MMA predictions.

The user asked: {user_question}

This appears to be a general question. Respond helpfully and direct them to ask about
specific fights or events if appropriate. You can answer questions about:
- Specific fights ("who will win Jones vs Aspinall?")
- Consensus picks ("what are the top picks for UFC 309?")
- Finish predictions ("who is likely to win inside the distance?")
- Underdogs ("best underdog picks for UFC Vegas 100?")

RESPONSE:
"""


# ---------------------------------------------------------------------------
# ChatMMABot
# ---------------------------------------------------------------------------

class ChatMMABot:
    """Main chatbot: detects query type, fetches context, calls Claude."""

    def __init__(self, api_key: str):
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-6"
        self.optimizer = QueryOptimizer()
        self.generator = PromptGenerator()

    # ── query-type detection ────────────────────────────────────────────────

    def detect_query_type(self, question: str) -> tuple[str, dict]:
        q = question.lower()

        if any(
            kw in q
            for kw in [
                "inside the distance", "inside distance", "finish",
                "knockout", " ko ", "submission", "most likely to finish",
                "not go the distance",
            ]
        ):
            return ("inside_distance", {"event_name": self._extract_event_name(q)})

        if any(
            kw in q
            for kw in [
                "consensus", "top picks", "favorites", "who should win",
                "most likely to win", "best bets", "safest picks", "locks",
            ]
        ):
            return ("consensus_picks", {"event_name": self._extract_event_name(q)})

        if any(
            kw in q
            for kw in [
                "underdog", "upset", "dark horse", "value pick", "sleeper",
                "best underdog", "undervalued", "contrarian",
            ]
        ):
            return ("underdogs", {"event_name": self._extract_event_name(q)})

        for sep in [" vs ", " vs. ", " versus ", " v ", " against "]:
            if sep in q:
                parts = q.split(sep)
                if len(parts) >= 2:
                    left_words = parts[0].strip().split()
                    right_words = parts[1].strip().split()
                    fa_words = left_words[-2:] if len(left_words) >= 2 else left_words[-1:]
                    fb_words = right_words[:2] if len(right_words) >= 2 else right_words[:1]
                    fa = re.sub(r"[^\w\s'\-]", "", " ".join(fa_words)).strip().title()
                    fb = re.sub(r"[^\w\s'\-]", "", " ".join(fb_words)).strip().title()
                    return (
                        "fight_specific",
                        {"fighter_a": fa, "fighter_b": fb, "event_name": self._extract_event_name(q)},
                    )

        return ("general", {})

    def _extract_event_name(self, q: str) -> str | None:
        # 1. Numbered/city events: UFC 324, UFC Vegas 100, UFC Fight Night 100
        m = re.search(r"ufc\s+(\d+|vegas\s+\d+|fight\s+night\s+\d+)", q)
        if m:
            return f"UFC {m.group(1).title()}"

        # 2. Named city events: "UFC Houston", "UFC London"
        m = re.search(r"ufc\s+([a-z][a-z\s]{1,25}?)(?:\s|$|[?!.,])", q)
        if m:
            candidate = m.group(1).strip()
            _skip = {
                "the", "this", "that", "a", "an", "in", "at", "for", "my",
                "picks", "fights", "card", "event", "show", "odds", "fight",
            }
            if candidate not in _skip:
                db = get_supabase()
                resp = (
                    db.table("events")
                    .select("name")
                    .ilike("name", f"%{candidate}%")
                    .order("date", desc=True)
                    .limit(1)
                    .execute()
                )
                if resp.data:
                    return resp.data[0]["name"]

        # 3. Fall back to most recent event
        db = get_supabase()
        resp = (
            db.table("events")
            .select("name")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0]["name"] if resp.data else None

    # ── query handlers ───────────────────────────────────────────────────────

    def answer_question(self, user_question: str) -> dict:
        query_type, details = self.detect_query_type(user_question)
        handlers = {
            "fight_specific":  self._handle_fight_specific,
            "inside_distance": self._handle_inside_distance,
            "consensus_picks": self._handle_consensus_picks,
            "underdogs":       self._handle_underdogs,
            "general":         self._handle_general,
        }
        return handlers[query_type](user_question, details)

    def _call_claude(self, prompt: str, max_tokens: int = 800) -> tuple[str, dict]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        cost = self._estimate_cost(response.usage)
        return response.content[0].text, cost

    def _handle_fight_specific(self, question: str, details: dict) -> dict:
        fa, fb = details["fighter_a"], details["fighter_b"]
        fight = self.optimizer.get_fight_by_fighters(fa, fb, details.get("event_name"))

        if not fight:
            return {
                "answer": (
                    f"I couldn't find a fight between **{fa}** and **{fb}** in the database. "
                    "Try checking the spelling or adding the event name (e.g. 'Jones vs Aspinall at UFC 309')."
                ),
                "metadata": {"query_type": "fight_not_found"},
            }

        context = self.optimizer.aggregate_fight_context(fight["fight_id"], fight)
        if not context or context["summary"]["total_predictions"] == 0:
            return {
                "answer": (
                    f"Found **{fight['fighter_a']} vs {fight['fighter_b']}** at {fight['event']}, "
                    "but there are no analyst predictions yet for this fight. "
                    "Try ingesting prediction articles from the URL Ingestion page."
                ),
                "metadata": {"query_type": "no_predictions"},
            }

        prompt = self.generator.build_fight_analysis_prompt(context, question)
        answer, cost = self._call_claude(prompt, max_tokens=900)
        return {
            "answer": answer,
            "metadata": {
                "query_type": "fight_analysis",
                "fight": fight,
                "cost_estimate": cost,
            },
        }

    def _handle_inside_distance(self, question: str, details: dict) -> dict:
        event_name = details.get("event_name")
        if not event_name:
            return {
                "answer": "Please specify an event (e.g. 'UFC 309') to get inside-distance predictions.",
                "metadata": {"query_type": "missing_event"},
            }
        context = self.optimizer.get_inside_distance_picks(event_name)
        if not context:
            return {
                "answer": f"I don't have predictions for **{event_name}** yet.",
                "metadata": {"query_type": "event_not_found"},
            }
        prompt = self.generator.build_inside_distance_prompt(context, question)
        answer, cost = self._call_claude(prompt, max_tokens=800)
        return {
            "answer": answer,
            "metadata": {"query_type": "inside_distance", "cost_estimate": cost},
        }

    def _handle_consensus_picks(self, question: str, details: dict) -> dict:
        event_name = details.get("event_name")
        if not event_name:
            return {
                "answer": "Please specify an event (e.g. 'UFC 309') to get consensus picks.",
                "metadata": {"query_type": "missing_event"},
            }
        context = self.optimizer.get_event_consensus_picks(event_name)
        if not context:
            return {
                "answer": f"I don't have predictions for **{event_name}** yet.",
                "metadata": {"query_type": "event_not_found"},
            }
        if not context["consensus_picks"]:
            return {
                "answer": f"Found **{event_name}**, but not enough predictions to determine consensus yet.",
                "metadata": {"query_type": "no_consensus"},
            }
        prompt = self.generator.build_consensus_picks_prompt(context, question)
        answer, cost = self._call_claude(prompt, max_tokens=1000)
        return {
            "answer": answer,
            "metadata": {"query_type": "consensus_picks", "cost_estimate": cost},
        }

    def _handle_underdogs(self, question: str, details: dict) -> dict:
        event_name = details.get("event_name")
        if not event_name:
            return {
                "answer": "Please specify an event (e.g. 'UFC 309') to get underdog picks.",
                "metadata": {"query_type": "missing_event"},
            }
        context = self.optimizer.get_event_underdogs(event_name)
        if not context:
            return {
                "answer": f"I don't have predictions for **{event_name}** yet.",
                "metadata": {"query_type": "event_not_found"},
            }
        if not context["underdog_picks"]:
            return {
                "answer": f"Found **{event_name}**, but no clear underdog opportunities — consensus is strong across all fights.",
                "metadata": {"query_type": "no_underdogs"},
            }
        prompt = self.generator.build_underdogs_prompt(context, question)
        answer, cost = self._call_claude(prompt, max_tokens=1000)
        return {
            "answer": answer,
            "metadata": {"query_type": "underdogs", "cost_estimate": cost},
        }

    def _handle_general(self, question: str, details: dict) -> dict:
        prompt = self.generator.build_general_prompt(question)
        answer, cost = self._call_claude(prompt, max_tokens=400)
        return {
            "answer": answer,
            "metadata": {"query_type": "general", "cost_estimate": cost},
        }

    # ── cost estimation ─────────────────────────────────────────────────────

    @staticmethod
    def _estimate_cost(usage) -> dict:
        input_cost = (usage.input_tokens / 1_000_000) * 3.0
        output_cost = (usage.output_tokens / 1_000_000) * 15.0
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "cost_usd": round(input_cost + output_cost, 5),
        }
