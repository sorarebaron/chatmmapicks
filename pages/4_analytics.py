"""
Analytics — analyst leaderboard, prediction accuracy, and scorecard breakdowns.

Four tabs:
  1. Leaderboard     — ranked pick accuracy per analyst
  2. Method          — method prediction rates and outcome distribution
  3. Event Breakdown — fight-by-fight consensus and pick detail
  4. Officials       — referee and judge appearances with scorecard tendencies
"""

import unicodedata

import streamlit as st
import pandas as pd
from collections import defaultdict
from rapidfuzz import fuzz

from utils.db import get_all_analytics_data


def _norm(name: str) -> str:
    """Lowercase and strip diacritics for fighter name comparison (e.g. é→e, ñ→n)."""
    nfd = unicodedata.normalize("NFD", name)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower().strip()


def _resolve_side(name_norm: str, fa_norm: str, fb_norm: str, threshold: int = 60) -> str | None:
    """Return 'a', 'b', or None — which fighter this name refers to.

    Uses exact normalized match first, then falls back to fuzzy matching.
    This handles spelling variants (e.g. 'Allin Perez' → 'Ailín Pérez') that
    survive diacritic stripping but differ by a character or two.
    """
    if name_norm == fa_norm:
        return "a"
    if name_norm == fb_norm:
        return "b"
    score_a = fuzz.WRatio(name_norm, fa_norm) if fa_norm else 0
    score_b = fuzz.WRatio(name_norm, fb_norm) if fb_norm else 0
    if max(score_a, score_b) < threshold:
        return None
    return "a" if score_a >= score_b else "b"


# ── Data loading & assembly ────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _load_raw() -> dict:
    """Batch-fetch all analytics source data (cached 5 min)."""
    return get_all_analytics_data()


def _build_rows(raw: dict) -> list[dict]:
    """Join picks → fights → results → events into flat analysis rows."""
    event_map = {e["event_id"]: e for e in raw["events"]}
    fight_map = {f["fight_id"]: f for f in raw["fights"]}
    result_map = {r["fight_id"]: r for r in raw["results"]}

    rows = []
    for pick in raw["picks"]:
        fight = fight_map.get(pick["fight_id"])
        if not fight:
            continue
        event = event_map.get(fight.get("event_id"), {})
        result = result_map.get(pick["fight_id"])

        winner = (result.get("winner") or "").strip() if result else ""
        picked = (pick.get("picked_fighter") or "").strip()
        actual_method = (result.get("method") or "").strip() if result else ""
        predicted_method = (pick.get("method_prediction") or "").strip()

        # Fighter pick correctness:
        #   None  = no result yet, or result was NC/Draw (treat as push)
        #   True  = analyst picked the winner
        #   False = analyst picked the loser
        #
        # We resolve which fighter each name refers to (fighter_a vs fighter_b)
        # using fuzzy matching so that spelling variants (e.g. "Allin Perez"
        # vs the DB's "Ailín Pérez") are still matched correctly.
        # The winner is always stored as exactly fighter_a or fighter_b (from
        # the Results Entry selectbox), so side-resolution is the reliable path.
        is_nc = winner.lower() in ("nc / draw", "nc", "draw", "") if winner else True
        correct: bool | None = None
        if result and winner and not is_nc and picked:
            fa_norm = _norm(fight.get("fighter_a") or "")
            fb_norm = _norm(fight.get("fighter_b") or "")
            winner_side = _resolve_side(_norm(winner), fa_norm, fb_norm)
            picked_side = _resolve_side(_norm(picked), fa_norm, fb_norm)
            if winner_side is not None and picked_side is not None:
                correct = winner_side == picked_side
            else:
                # Fallback: direct normalized comparison
                correct = _norm(winner) == _norm(picked)

        # Method prediction correctness:
        #   None  = no result yet, or either side has no method data
        method_correct: bool | None = None
        if result and actual_method and predicted_method:
            method_correct = actual_method.lower() == predicted_method.lower()

        rows.append({
            "pick_id": pick["pick_id"],
            "analyst": pick.get("analyst_name") or "",
            "platform": pick.get("platform") or pick.get("analyst_name") or "",
            "event_id": fight.get("event_id") or "",
            "event": event.get("name") or "",
            "event_date": event.get("date") or "",
            "fight_id": fight["fight_id"],
            "fighter_a": fight.get("fighter_a") or "",
            "fighter_b": fight.get("fighter_b") or "",
            "weight_class": fight.get("weight_class") or "",
            "title_fight": fight.get("title_fight") or False,
            "bout_order": fight.get("bout_order"),
            "picked_fighter": picked,
            "method_prediction": predicted_method,
            "has_result": result is not None,
            "winner": winner,
            "actual_method": actual_method,
            "correct": correct,
            "method_correct": method_correct,
        })

    return rows


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _pct(n: int, d: int) -> str:
    return f"{n / d * 100:.1f}%" if d else "—"


def _pct_f(n: int, d: int) -> float | None:
    return n / d * 100 if d else None


def _acc(rows: list[dict], field: str = "correct") -> tuple[int, int]:
    """Return (correct_count, evaluated_count) for rows where field is not None."""
    evaluated = [r for r in rows if r[field] is not None]
    correct = [r for r in evaluated if r[field]]
    return len(correct), len(evaluated)


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


# ═══════════════════════════════════════════════════════════════════════════════
# Page
# ═══════════════════════════════════════════════════════════════════════════════

st.title("Analytics")
st.caption("Analyst leaderboard, prediction accuracy, and scorecard breakdowns.")

raw = _load_raw()
all_rows = _build_rows(raw)

if not all_rows:
    st.info("No picks found. Use **URL Ingestion** to load predictions.")
    st.stop()

if not any(r["correct"] is not None for r in all_rows):
    st.info("No results recorded yet. Enter results on the **Results Entry** page to unlock analytics.")
    st.stop()


# ── Sidebar filters ────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filters")
    st.caption("Applies to Leaderboard and Method tabs.")

    all_event_names = sorted({r["event"] for r in all_rows if r["event"]})
    sel_events = st.multiselect("Event", options=all_event_names, default=all_event_names, key="an_ev")

    all_wc = sorted({r["weight_class"] for r in all_rows if r["weight_class"]})
    sel_wc = st.multiselect("Weight class", options=all_wc, default=all_wc, key="an_wc")

    title_only = st.checkbox("Title fights only", value=False, key="an_tf")


def _apply_filters(rows: list[dict]) -> list[dict]:
    out = rows
    if sel_events:
        out = [r for r in out if r["event"] in sel_events]
    if sel_wc:
        out = [r for r in out if r["weight_class"] in sel_wc]
    if title_only:
        out = [r for r in out if r["title_fight"]]
    return out


rows = _apply_filters(all_rows)

if not rows:
    st.warning("No data matches the current filters.")
    st.stop()


# ── KPI cards ──────────────────────────────────────────────────────────────────

ev_rows = [r for r in rows if r["correct"] is not None]
corr_rows = [r for r in ev_rows if r["correct"]]
analysts = sorted({r["analyst"] for r in rows})
events_shown = {r["event"] for r in rows}

k1, k2, k3, k4 = st.columns(4)
k1.metric("Picks Evaluated", len(ev_rows))
k2.metric("Overall Accuracy", _pct(len(corr_rows), len(ev_rows)))
k3.metric("Analysts", len(analysts))
k4.metric("Events", len(events_shown))

st.divider()


# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_lb, tab_cm, tab_ev, tab_off = st.tabs(
    ["Leaderboard", "Method", "Event Breakdown", "Officials"]
)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 · Leaderboard
# ─────────────────────────────────────────────────────────────────────────────

with tab_lb:
    st.subheader("Analyst Leaderboard")
    st.caption("Ranked by pick accuracy across fights with recorded results. Method Acc. = % correct on method predictions.")

    lb = []
    for analyst in analysts:
        a = [r for r in rows if r["analyst"] == analyst]
        c, e = _acc(a)
        mc, me = _acc(a, "method_correct")
        platform = a[0]["platform"] if a else ""
        lb.append({
            "#": 0,
            "Analyst": analyst,
            "Platform": platform,
            "Total Picks": len(a),
            "Evaluated": e,
            "Correct": c,
            "Accuracy": _pct_f(c, e),
            "Method Acc.": _pct_f(mc, me),
        })

    lb.sort(key=lambda x: (x["Accuracy"] if x["Accuracy"] is not None else -1, x["Evaluated"]), reverse=True)
    for i, row in enumerate(lb, 1):
        row["#"] = i

    df_lb = pd.DataFrame(lb)[["#", "Analyst", "Platform", "Total Picks", "Evaluated", "Correct", "Accuracy", "Method Acc."]]
    st.dataframe(
        df_lb.style.format({"Accuracy": _fmt_pct, "Method Acc.": _fmt_pct}),
        use_container_width=True,
        hide_index=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 · Method
# ─────────────────────────────────────────────────────────────────────────────

with tab_cm:
    st.subheader("Method Prediction Accuracy")
    method_table = []
    for method in ["KO/TKO", "Submission", "Decision", "NC", "DQ"]:
        predicted = [r for r in rows if r["method_prediction"] == method]
        if not predicted:
            continue
        mc, me = _acc(predicted, "method_correct")
        actual_ct = sum(
            1 for fid in {r["fight_id"] for r in all_rows if r["actual_method"] == method}
        )
        method_table.append({
            "Method": method,
            "Predicted": len(predicted),
            "Evaluated": me,
            "Correct": mc,
            "Accuracy": _pct(mc, me),
            "Actually Happened": actual_ct,
        })
    if method_table:
        st.dataframe(pd.DataFrame(method_table), use_container_width=True, hide_index=True)
    else:
        st.info("No method prediction data available.")



# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 · Event Breakdown
# ─────────────────────────────────────────────────────────────────────────────

with tab_ev:
    st.subheader("Fight-by-Fight Breakdown")
    st.caption("Uses all picks and results for the selected event, independent of sidebar filters.")

    ev_date_map = {r["event"]: r["event_date"] for r in all_rows if r["event"]}
    ev_options = sorted(ev_date_map, key=lambda e: ev_date_map[e], reverse=True)
    if not ev_options:
        st.info("No events available.")
    else:
        sel_ev = st.selectbox("Select event", options=ev_options, key="an_ev_sel")

        ev_all = [r for r in all_rows if r["event"] == sel_ev]
        fight_groups: dict[str, list[dict]] = defaultdict(list)
        for r in ev_all:
            fight_groups[r["fight_id"]].append(r)

        sorted_fids = sorted(
            fight_groups.keys(),
            key=lambda fid: (
                fight_groups[fid][0]["bout_order"] is None,
                fight_groups[fid][0]["bout_order"] or 0,
            ),
        )

        # Event-level summary KPIs
        ev_evaluated = [r for r in ev_all if r["correct"] is not None]
        ev_correct = [r for r in ev_evaluated if r["correct"]]
        ek1, ek2, ek3 = st.columns(3)
        ek1.metric("Fights on card", len(fight_groups))
        ek2.metric("Picks evaluated", len(ev_evaluated))
        ek3.metric("Accuracy", _pct(len(ev_correct), len(ev_evaluated)))

        st.divider()

        for fid in sorted_fids:
            fr = fight_groups[fid]
            fa = fr[0]["fighter_a"]
            fb = fr[0]["fighter_b"]
            wc = fr[0]["weight_class"] or "?"
            has_result = fr[0]["has_result"]
            winner = fr[0]["winner"]
            act_method = fr[0]["actual_method"]
            title = fr[0]["title_fight"]

            fa_norm = _norm(fa)
            fb_norm = _norm(fb)
            picks_a = sum(1 for r in fr if _resolve_side(_norm(r["picked_fighter"]), fa_norm, fb_norm) == "a")
            picks_b = sum(1 for r in fr if _resolve_side(_norm(r["picked_fighter"]), fa_norm, fb_norm) == "b")
            total_picks = picks_a + picks_b

            if total_picks > 0:
                consensus_fighter = fa if picks_a > picks_b else (fb if picks_b > picks_a else None)
            else:
                consensus_fighter = None

            is_nc = winner.lower() in ("nc / draw", "nc", "draw") if winner else False

            if has_result and winner and not is_nc:
                winner_side = _resolve_side(_norm(winner), fa_norm, fb_norm)
                consensus_side = "a" if consensus_fighter == fa else ("b" if consensus_fighter == fb else None)
                consensus_hit = winner_side is not None and consensus_side is not None and winner_side == consensus_side
                result_icon = "✅" if consensus_hit else "❌"
            elif has_result and is_nc:
                result_icon = "🔄"
            else:
                result_icon = "⬜"

            bout_str = f"  ·  bout #{fr[0]['bout_order']}" if fr[0].get("bout_order") else ""
            title_str = "  🏆" if title else ""
            header = f"{result_icon}  **{fa} vs {fb}**  ·  {wc}{bout_str}{title_str}"
            if has_result and winner:
                method_str = f" via {act_method}" if act_method else ""
                header += f"  —  **{winner}**{method_str}"

            with st.expander(header, expanded=False):
                if total_picks > 0:
                    bar_label = (
                        f"{picks_a} → {fa}  |  {picks_b} → {fb}  "
                        f"({total_picks} total picks)"
                    )
                    st.progress(picks_a / total_picks, text=bar_label)

                pick_rows = []
                for r in sorted(fr, key=lambda x: x["analyst"]):
                    if has_result and not is_nc and r["correct"] is not None:
                        outcome = "✅" if r["correct"] else "❌"
                    elif has_result and is_nc:
                        outcome = "🔄"
                    else:
                        outcome = "⬜"
                    pick_rows.append({
                        "Analyst": r["analyst"],
                        "Picked": r["picked_fighter"],
                        "Method": r["method_prediction"] or "—",
                        "Result": outcome,
                    })
                st.dataframe(pd.DataFrame(pick_rows), use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 · Officials
# ─────────────────────────────────────────────────────────────────────────────

with tab_off:
    st.subheader("Officials")
    st.caption(
        "Referees and judges across all recorded results. "
        "An official can appear in both roles (e.g. a referee who also judged a fight)."
    )

    fight_map_off = {f["fight_id"]: f for f in raw["fights"]}
    event_map_off = {e["event_id"]: e for e in raw["events"]}

    officials: dict[str, dict] = {}

    for res in raw["results"]:
        fight = fight_map_off.get(res["fight_id"], {})
        event = event_map_off.get(fight.get("event_id"), {})
        fight_label = f"{fight.get('fighter_a', '')} vs {fight.get('fighter_b', '')}"
        event_label = event.get("name", "")

        ref = (res.get("referee") or "").strip()
        if ref:
            if ref not in officials:
                officials[ref] = {"roles": set(), "fights": []}
            officials[ref]["roles"].add("Referee")
            officials[ref]["fights"].append({
                "event": event_label,
                "fight": fight_label,
                "role": "Referee",
                "score": "",
            })

        for j in [1, 2, 3]:
            jname = (res.get(f"judge{j}_name") or "").strip()
            jscore = (res.get(f"judge{j}_score") or "").strip()
            if jname:
                if jname not in officials:
                    officials[jname] = {"roles": set(), "fights": []}
                officials[jname]["roles"].add("Judge")
                officials[jname]["fights"].append({
                    "event": event_label,
                    "fight": fight_label,
                    "role": "Judge",
                    "score": jscore,
                })

    if not officials:
        st.info("No officials recorded yet. Enter referee and judge data on the **Results Entry** page.")
    else:
        # Summary table
        summary_rows = []
        for name, info in sorted(officials.items()):
            roles_str = " / ".join(sorted(info["roles"]))
            judge_fights = [f for f in info["fights"] if f["role"] == "Judge"]
            ref_fights = [f for f in info["fights"] if f["role"] == "Referee"]
            scores = [f["score"] for f in judge_fights if f["score"]]
            unique_scores = sorted(set(scores))
            score_summary = ", ".join(unique_scores) if unique_scores else "—"
            summary_rows.append({
                "Official": name,
                "Role(s)": roles_str,
                "Fights Reffed": len(ref_fights),
                "Fights Judged": len(judge_fights),
                "Scores Given": score_summary,
            })
        summary_rows.sort(
            key=lambda x: (x["Fights Judged"] + x["Fights Reffed"]),
            reverse=True,
        )
        st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Detail by Official")

        for name, info in sorted(officials.items(), key=lambda x: -len(x[1]["fights"])):
            roles_str = " / ".join(sorted(info["roles"]))
            fight_count = len(info["fights"])
            with st.expander(f"**{name}** — {roles_str} · {fight_count} fight(s)", expanded=False):
                detail_rows = []
                for f in sorted(info["fights"], key=lambda x: (x["event"], x["fight"])):
                    detail_rows.append({
                        "Event": f["event"],
                        "Fight": f["fight"],
                        "Role": f["role"],
                        "Score": f["score"] or "—",
                    })
                st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
