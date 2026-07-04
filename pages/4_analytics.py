"""
Analytics — analyst leaderboard, prediction accuracy, and scorecard breakdowns.

Five tabs:
  1. Overview        — accuracy trend, consensus strength, favorites vs underdogs, upsets
  2. Leaderboard     — ranked pick accuracy per analyst with streaks, form, and dog accuracy
  3. Method          — method prediction rates, predicted-vs-actual mix, finish patterns
  4. Event Breakdown — fight-by-fight consensus and pick detail
  5. Officials       — referee and judge appearances with scorecard tendencies

Charts use Altair (bundled with Streamlit) with a palette validated for the
app's dark surface (#1A1A1A): series colors pass the lightness band, chroma
floor, and 3:1 contrast checks; multi-series charts carry legends + labels.
"""

import unicodedata

import altair as alt
import pandas as pd
import streamlit as st
from collections import defaultdict
from rapidfuzz import fuzz

from utils.db import get_all_analytics_data

# ── Chart palette (validated for dark surface #1A1A1A) ────────────────────────

SERIES_1 = "#3987E5"   # blue   — primary series / single-series bars & lines
SERIES_2 = "#199E70"   # aqua   — second series
SERIES_3 = "#C98500"   # yellow — third series (comparison lines only)
SERIES_4 = "#008300"   # green  — fourth series (comparison lines only)
INK_SECONDARY = "#C3C2B7"
INK_MUTED = "#898781"
GRIDLINE = "#2A2A2A"
BASELINE = "#383835"

_COMPARE_SLOTS = [SERIES_1, SERIES_2, SERIES_3, SERIES_4]


def _dark(chart: alt.Chart) -> alt.Chart:
    """Apply the recessive dark-theme chrome: hairline grid, muted axis ink."""
    return (
        chart
        .configure(background="transparent")
        .configure_axis(
            gridColor=GRIDLINE,
            domainColor=BASELINE,
            tickColor=BASELINE,
            labelColor=INK_MUTED,
            titleColor=INK_SECONDARY,
            labelFontSize=11,
            titleFontSize=12,
        )
        .configure_legend(
            labelColor=INK_SECONDARY,
            titleColor=INK_MUTED,
            orient="bottom",
        )
        .configure_view(stroke=None)
    )


# ── Name / odds helpers ────────────────────────────────────────────────────────

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


def _fav_side(a_odds: int | None, b_odds: int | None) -> str | None:
    """Return 'a'/'b' for the betting favorite by implied probability, None if unknown."""
    if a_odds is None or b_odds is None:
        return None

    def prob(o: int) -> float:
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)

    if prob(a_odds) == prob(b_odds):
        return None
    return "a" if prob(a_odds) > prob(b_odds) else "b"


def _fmt_odds(odds: int | None) -> str:
    if odds is None:
        return "—"
    return f"+{odds}" if odds > 0 else str(odds)


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

        fa_norm = _norm(fight.get("fighter_a") or "")
        fb_norm = _norm(fight.get("fighter_b") or "")

        a_odds = fight.get("fighter_a_win_odds")
        b_odds = fight.get("fighter_b_win_odds")
        fav_side = _fav_side(a_odds, b_odds)

        picked_side = _resolve_side(_norm(picked), fa_norm, fb_norm) if picked else None
        picked_favorite: bool | None = None
        if picked_side is not None and fav_side is not None:
            picked_favorite = picked_side == fav_side

        # Fighter pick correctness:
        #   None  = no result yet, or result was NC/Draw (treat as push)
        #   True  = analyst picked the winner
        #   False = analyst picked the loser
        #
        # The winner is always stored as exactly fighter_a or fighter_b (from
        # the Results Entry selectbox), so side-resolution is the reliable path.
        is_nc = winner.lower() in ("nc / draw", "nc", "draw", "") if winner else True
        correct: bool | None = None
        winner_side: str | None = None
        if result and winner and not is_nc and picked:
            winner_side = _resolve_side(_norm(winner), fa_norm, fb_norm)
            if winner_side is not None and picked_side is not None:
                correct = winner_side == picked_side
            else:
                # Fallback: direct normalized comparison
                correct = _norm(winner) == _norm(picked)

        # Method prediction correctness:
        #   None  = no result yet, or couldn't resolve fighter
        #   True  = analyst predicted the correct method AND the correct fighter
        #   False = wrong method, or right method but wrong fighter
        #
        # For NC/Draw fights there is no winner, so method alone is evaluated.
        method_correct: bool | None = None
        if result and actual_method and predicted_method:
            method_match = actual_method.lower() == predicted_method.lower()
            if is_nc:
                # No winner — method prediction stands on its own
                method_correct = method_match
            elif correct is not None:
                # Must predict both the right winner AND the right method
                method_correct = method_match and correct

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
            "actual_round": result.get("round") if result else None,
            "correct": correct,
            "method_correct": method_correct,
            "picked_side": picked_side,
            "winner_side": winner_side,
            "fav_side": fav_side,
            "picked_favorite": picked_favorite,
            "a_odds": a_odds,
            "b_odds": b_odds,
        })

    return rows


def _fight_summaries(pick_rows: list[dict]) -> list[dict]:
    """Collapse pick rows into one summary per fight (consensus, result, odds)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in pick_rows:
        groups[r["fight_id"]].append(r)

    out = []
    for fid, fr in groups.items():
        f0 = fr[0]
        fa_norm = _norm(f0["fighter_a"])
        fb_norm = _norm(f0["fighter_b"])
        picks_a = sum(1 for r in fr if r["picked_side"] == "a")
        picks_b = sum(1 for r in fr if r["picked_side"] == "b")
        total = picks_a + picks_b

        consensus_side: str | None = None
        consensus_share: float | None = None
        if total > 0 and picks_a != picks_b:
            consensus_side = "a" if picks_a > picks_b else "b"
            consensus_share = max(picks_a, picks_b) / total * 100

        winner = f0["winner"]
        is_nc = winner.lower() in ("nc / draw", "nc", "draw") if winner else False
        winner_side = None
        if f0["has_result"] and winner and not is_nc:
            winner_side = _resolve_side(_norm(winner), fa_norm, fb_norm)

        consensus_correct: bool | None = None
        if consensus_side is not None and winner_side is not None:
            consensus_correct = consensus_side == winner_side

        fav_side = f0["fav_side"]
        favorite_won: bool | None = None
        if fav_side is not None and winner_side is not None:
            favorite_won = fav_side == winner_side

        out.append({
            "fight_id": fid,
            "event": f0["event"],
            "event_date": f0["event_date"],
            "fighter_a": f0["fighter_a"],
            "fighter_b": f0["fighter_b"],
            "weight_class": f0["weight_class"],
            "bout_order": f0["bout_order"],
            "title_fight": f0["title_fight"],
            "has_result": f0["has_result"],
            "winner": winner,
            "winner_side": winner_side,
            "actual_method": f0["actual_method"],
            "actual_round": f0["actual_round"],
            "picks_a": picks_a,
            "picks_b": picks_b,
            "total_picks": total,
            "consensus_side": consensus_side,
            "consensus_share": consensus_share,
            "consensus_correct": consensus_correct,
            "fav_side": fav_side,
            "favorite_won": favorite_won,
            "a_odds": f0["a_odds"],
            "b_odds": f0["b_odds"],
        })
    return out


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
    st.caption("Applies to Overview, Leaderboard, and Method tabs.")

    event_date_map = {r["event"]: r["event_date"] for r in all_rows if r["event"]}
    all_event_names = sorted(event_date_map, key=lambda e: event_date_map[e], reverse=True)
    sel_events = st.multiselect("Event", options=all_event_names, default=all_event_names, key="an_ev")

    all_wc = sorted({r["weight_class"] for r in all_rows if r["weight_class"]})
    sel_wc = st.multiselect("Weight class", options=all_wc, default=all_wc, key="an_wc")

    title_only = st.checkbox("Title fights only", value=False, key="an_tf")

    st.divider()
    min_picks = st.slider(
        "Min. evaluated picks (Leaderboard)",
        min_value=1, max_value=30, value=3, key="an_minp",
        help="Hide analysts with fewer evaluated picks than this from the leaderboard.",
    )


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

fights = _fight_summaries(rows)


# ── KPI cards ──────────────────────────────────────────────────────────────────

ev_rows = [r for r in rows if r["correct"] is not None]
corr_rows = [r for r in ev_rows if r["correct"]]
analysts = sorted({r["analyst"] for r in rows})
events_shown = {r["event"] for r in rows}

fav_fights = [f for f in fights if f["favorite_won"] is not None]
fav_won = [f for f in fav_fights if f["favorite_won"]]
overall_acc = _pct_f(len(corr_rows), len(ev_rows))
fav_rate = _pct_f(len(fav_won), len(fav_fights))

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Picks Evaluated", len(ev_rows))
if overall_acc is not None and fav_rate is not None:
    k2.metric(
        "Overall Accuracy", _fmt_pct(overall_acc),
        delta=f"{overall_acc - fav_rate:+.1f} pts vs chalk",
        help="Delta compares analyst accuracy to 'always pick the betting favorite'.",
    )
else:
    k2.metric("Overall Accuracy", _fmt_pct(overall_acc))
k3.metric(
    "Favorite Win Rate", _fmt_pct(fav_rate),
    help="How often the betting favorite actually won (fights with odds + result).",
)
k4.metric("Analysts", len(analysts))
k5.metric("Events", len(events_shown))

st.divider()


# ── Tabs ───────────────────────────────────────────────────────────────────────

tab_ov, tab_lb, tab_cm, tab_ev, tab_off = st.tabs(
    ["Overview", "Leaderboard", "Method", "Event Breakdown", "Officials"]
)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 · Overview
# ─────────────────────────────────────────────────────────────────────────────

with tab_ov:

    # ── Accuracy trend by event ──────────────────────────────────────────────
    st.subheader("Accuracy by Event")
    st.caption("How the analyst field performed card by card. The dashed rule is the all-time average.")

    ev_trend = []
    for ev_name in {r["event"] for r in rows if r["event_date"]}:
        er = [r for r in rows if r["event"] == ev_name and r["correct"] is not None]
        if not er:
            continue
        c = sum(1 for r in er if r["correct"])
        ev_trend.append({
            "event": ev_name,
            "date": er[0]["event_date"],
            "accuracy": c / len(er) * 100,
            "record": f"{c}/{len(er)}",
        })

    if len(ev_trend) >= 2:
        df_trend = pd.DataFrame(ev_trend).sort_values("date")
        base = alt.Chart(df_trend).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %Y")),
        )
        line = base.mark_line(
            strokeWidth=2, color=SERIES_1,
            point=alt.OverlayMarkDef(size=70, color=SERIES_1),
        ).encode(
            y=alt.Y("accuracy:Q", title="Accuracy (%)", scale=alt.Scale(domain=[0, 100])),
            tooltip=[
                alt.Tooltip("event:N", title="Event"),
                alt.Tooltip("date:T", title="Date"),
                alt.Tooltip("accuracy:Q", title="Accuracy", format=".1f"),
                alt.Tooltip("record:N", title="Record"),
            ],
        )
        avg_val = overall_acc if overall_acc is not None else df_trend["accuracy"].mean()
        rule = alt.Chart(pd.DataFrame({"y": [avg_val]})).mark_rule(
            color=INK_MUTED, strokeDash=[4, 4], strokeWidth=1,
        ).encode(y="y:Q")
        st.altair_chart(_dark(alt.layer(line, rule).properties(height=280)),
                        use_container_width=True, theme=None)
    else:
        st.info("Need at least two dated events with results to draw the trend.")

    st.divider()

    # ── Consensus strength vs. accuracy + favorites/underdogs ────────────────
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.subheader("When the Crowd Agrees")
        st.caption("Accuracy of the consensus pick, bucketed by how strongly analysts agreed.")

        bucket_order = ["Unanimous", "Strong (75–99%)", "Lean (60–74%)", "Split (<60%)"]

        def _bucket(share: float) -> str:
            if share >= 100:
                return "Unanimous"
            if share >= 75:
                return "Strong (75–99%)"
            if share >= 60:
                return "Lean (60–74%)"
            return "Split (<60%)"

        cons_fights = [f for f in fights if f["consensus_correct"] is not None]
        bucket_rows = []
        for b in bucket_order:
            bf = [f for f in cons_fights if _bucket(f["consensus_share"]) == b]
            if not bf:
                continue
            hits = sum(1 for f in bf if f["consensus_correct"])
            bucket_rows.append({
                "bucket": b,
                "accuracy": hits / len(bf) * 100,
                "record": f"{hits}/{len(bf)} fights",
                "n": len(bf),
            })

        if bucket_rows:
            df_b = pd.DataFrame(bucket_rows)
            bars = alt.Chart(df_b).mark_bar(
                color=SERIES_1, size=36,
                cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
            ).encode(
                x=alt.X("bucket:N", sort=bucket_order, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("accuracy:Q", title="Consensus accuracy (%)", scale=alt.Scale(domain=[0, 100])),
                tooltip=[
                    alt.Tooltip("bucket:N", title="Agreement"),
                    alt.Tooltip("accuracy:Q", title="Accuracy", format=".1f"),
                    alt.Tooltip("record:N", title="Record"),
                ],
            )
            labels = alt.Chart(df_b).mark_text(
                dy=-8, color=INK_SECONDARY, fontSize=12,
            ).encode(
                x=alt.X("bucket:N", sort=bucket_order),
                y="accuracy:Q",
                text=alt.Text("accuracy:Q", format=".0f"),
            )
            st.altair_chart(_dark(alt.layer(bars, labels).properties(height=260)),
                            use_container_width=True, theme=None)
        else:
            st.info("No fights with both a consensus pick and a decisive result yet.")

    with col_r:
        st.subheader("Chalk vs. Value")
        st.caption("Analyst performance split by whether the pick was the betting favorite.")

        odds_rows = [r for r in ev_rows if r["picked_favorite"] is not None]
        fav_picks = [r for r in odds_rows if r["picked_favorite"]]
        dog_picks = [r for r in odds_rows if not r["picked_favorite"]]
        fav_c = sum(1 for r in fav_picks if r["correct"])
        dog_c = sum(1 for r in dog_picks if r["correct"])

        if odds_rows:
            m1, m2 = st.columns(2)
            m1.metric(
                "Acc. picking favorites", _pct(fav_c, len(fav_picks)),
                help=f"{fav_c}/{len(fav_picks)} evaluated picks on the betting favorite.",
            )
            m2.metric(
                "Acc. picking underdogs", _pct(dog_c, len(dog_picks)),
                help=f"{dog_c}/{len(dog_picks)} evaluated picks on the betting underdog.",
            )
            m3, m4 = st.columns(2)
            m3.metric(
                "Chalk rate", _pct(len(fav_picks), len(odds_rows)),
                help="Share of evaluated picks that sided with the betting favorite.",
            )
            m4.metric(
                "Underdog picks", len(dog_picks),
                help="Evaluated picks on the betting underdog.",
            )
        else:
            st.info("Enter win odds in QC / Editor to unlock chalk vs. value insight.")

    st.divider()

    # ── Consensus busts ──────────────────────────────────────────────────────
    st.subheader("Biggest Consensus Busts")
    st.caption("Fights where at least 70% of analysts agreed — and the crowd was wrong.")

    busts = [
        f for f in fights
        if f["consensus_correct"] is False and (f["consensus_share"] or 0) >= 70
    ]
    busts.sort(key=lambda f: f["consensus_share"], reverse=True)

    if busts:
        bust_rows = []
        for f in busts:
            cons_fighter = f["fighter_a"] if f["consensus_side"] == "a" else f["fighter_b"]
            winner_odds = f["a_odds"] if f["winner_side"] == "a" else f["b_odds"]
            bust_rows.append({
                "Event": f["event"],
                "Fight": f"{f['fighter_a']} vs {f['fighter_b']}",
                "Crowd Backed": f"{cons_fighter} ({f['consensus_share']:.0f}% of {f['total_picks']})",
                "Winner": f["winner"],
                "Winner Odds": _fmt_odds(winner_odds),
                "Method": f["actual_method"] or "—",
            })
        st.dataframe(pd.DataFrame(bust_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No consensus busts under the current filters — the crowd has held up.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 · Leaderboard
# ─────────────────────────────────────────────────────────────────────────────

with tab_lb:
    st.subheader("Analyst Leaderboard")
    st.caption(
        "Ranked by pick accuracy across fights with recorded results. "
        "Method Acc. = correct fighter AND method · Dog Acc. = accuracy on betting-underdog picks · "
        "Streak = current run of consecutive results · Form = accuracy over the analyst's last 3 events."
    )

    def _chrono_key(r: dict):
        # Chronological pick order: event date ascending; within an event,
        # prelims (higher bout numbers) happen before the main event (bout #1).
        return (r["event_date"] or "", -(r["bout_order"] or 0))

    lb = []
    for analyst in analysts:
        a = [r for r in rows if r["analyst"] == analyst]
        c, e = _acc(a)
        if e < min_picks:
            continue
        mc, me = _acc(a, "method_correct")
        platform = a[0]["platform"] if a else ""

        dog = [r for r in a if r["picked_favorite"] is False and r["correct"] is not None]
        dog_c = sum(1 for r in dog if r["correct"])

        # Current streak (most recent evaluated picks, walking backwards)
        seq = [r["correct"] for r in sorted(a, key=_chrono_key) if r["correct"] is not None]
        streak = ""
        if seq:
            last = seq[-1]
            n = 0
            for v in reversed(seq):
                if v != last:
                    break
                n += 1
            streak = f"{'🔥 ' if last and n >= 3 else ''}{n}{'W' if last else 'L'}"

        # Form: accuracy over the analyst's 3 most recent events with results
        ev_dates = sorted({r["event_date"] for r in a if r["correct"] is not None and r["event_date"]}, reverse=True)
        recent = [r for r in a if r["event_date"] in ev_dates[:3] and r["correct"] is not None]
        form = _pct_f(sum(1 for r in recent if r["correct"]), len(recent))

        lb.append({
            "#": 0,
            "Analyst": analyst,
            "Platform": platform,
            "Total": len(a),
            "Eval": e,
            "Correct": c,
            "Accuracy": _pct_f(c, e),
            "Method Acc.": _pct_f(mc, me),
            "Dog Acc.": _pct_f(dog_c, len(dog)),
            "Dog Picks": len(dog),
            "Streak": streak,
            "Form (L3)": form,
        })

    if not lb:
        st.info(f"No analysts with at least {min_picks} evaluated pick(s) under the current filters.")
    else:
        lb.sort(key=lambda x: (x["Accuracy"] if x["Accuracy"] is not None else -1, x["Eval"]), reverse=True)
        for i, row in enumerate(lb, 1):
            row["#"] = i

        df_lb = pd.DataFrame(lb)
        st.dataframe(
            df_lb,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Accuracy": st.column_config.ProgressColumn(
                    "Accuracy", format="%.1f%%", min_value=0, max_value=100,
                ),
                "Method Acc.": st.column_config.NumberColumn("Method Acc.", format="%.1f%%"),
                "Dog Acc.": st.column_config.NumberColumn("Dog Acc.", format="%.1f%%"),
                "Form (L3)": st.column_config.NumberColumn("Form (L3)", format="%.1f%%"),
            },
        )

        # ── Head-to-head trend ────────────────────────────────────────────────
        st.divider()
        st.subheader("Analyst Head-to-Head")
        st.caption("Cumulative accuracy over time. Compare up to 4 analysts.")

        ranked_names = [r["Analyst"] for r in lb]
        default_sel = ranked_names[:3]
        sel_analysts = st.multiselect(
            "Analysts to compare",
            options=ranked_names,
            default=default_sel,
            max_selections=4,
            key="an_cmp",
        )

        if len(sel_analysts) >= 2:
            trend_rows = []
            for name in sel_analysts:
                a = sorted(
                    [r for r in rows if r["analyst"] == name and r["correct"] is not None and r["event_date"]],
                    key=_chrono_key,
                )
                by_event: dict[str, list[dict]] = defaultdict(list)
                for r in a:
                    by_event[r["event_date"]].append(r)
                cum_c = cum_e = 0
                for d in sorted(by_event):
                    er = by_event[d]
                    cum_c += sum(1 for r in er if r["correct"])
                    cum_e += len(er)
                    trend_rows.append({
                        "analyst": name,
                        "date": d,
                        "event": er[0]["event"],
                        "acc": cum_c / cum_e * 100,
                        "record": f"{cum_c}/{cum_e}",
                    })

            if trend_rows:
                df_cmp = pd.DataFrame(trend_rows)
                # Color follows the entity: slots assigned by name order, stable
                # for a given selection regardless of rank changes.
                domain = sorted(sel_analysts)
                color_scale = alt.Scale(domain=domain, range=_COMPARE_SLOTS[: len(domain)])

                lines = alt.Chart(df_cmp).mark_line(
                    strokeWidth=2, point=alt.OverlayMarkDef(size=60),
                ).encode(
                    x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %Y")),
                    y=alt.Y("acc:Q", title="Cumulative accuracy (%)", scale=alt.Scale(domain=[0, 100])),
                    color=alt.Color("analyst:N", scale=color_scale, title=None),
                    tooltip=[
                        alt.Tooltip("analyst:N", title="Analyst"),
                        alt.Tooltip("event:N", title="Through event"),
                        alt.Tooltip("acc:Q", title="Cumulative acc.", format=".1f"),
                        alt.Tooltip("record:N", title="Record"),
                    ],
                )
                # Direct labels at line ends (ink color; the line carries identity)
                df_last = df_cmp.sort_values("date").groupby("analyst").tail(1)
                end_labels = alt.Chart(df_last).mark_text(
                    align="left", dx=8, fontSize=11, color=INK_SECONDARY,
                ).encode(x="date:T", y="acc:Q", text="analyst:N")

                st.altair_chart(_dark(alt.layer(lines, end_labels).properties(height=320)),
                                use_container_width=True, theme=None)
        else:
            st.info("Select at least two analysts to compare.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 · Method
# ─────────────────────────────────────────────────────────────────────────────

with tab_cm:
    st.subheader("Predicted vs. Actual Outcome Mix")
    st.caption(
        "Do analysts over-call finishes? Share of method predictions vs. the share of how "
        "fights with results actually ended."
    )

    method_names = ["KO/TKO", "Submission", "Decision"]
    pred_rows_m = [r for r in rows if r["method_prediction"] in method_names]
    result_fights = [f for f in fights if f["has_result"] and f["actual_method"] in method_names]

    if pred_rows_m and result_fights:
        mix = []
        for m in method_names:
            mix.append({
                "Method": m, "Kind": "Predicted",
                "share": sum(1 for r in pred_rows_m if r["method_prediction"] == m) / len(pred_rows_m) * 100,
                "n": sum(1 for r in pred_rows_m if r["method_prediction"] == m),
            })
            mix.append({
                "Method": m, "Kind": "Actual",
                "share": sum(1 for f in result_fights if f["actual_method"] == m) / len(result_fights) * 100,
                "n": sum(1 for f in result_fights if f["actual_method"] == m),
            })
        df_mix = pd.DataFrame(mix)
        kind_scale = alt.Scale(domain=["Predicted", "Actual"], range=[SERIES_1, SERIES_2])
        mix_chart = alt.Chart(df_mix).mark_bar(
            size=30, cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
        ).encode(
            x=alt.X("Method:N", sort=method_names, title=None, axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("Kind:N", scale=alt.Scale(paddingInner=0.15)),
            y=alt.Y("share:Q", title="Share (%)"),
            color=alt.Color("Kind:N", scale=kind_scale, title=None),
            tooltip=[
                alt.Tooltip("Method:N"),
                alt.Tooltip("Kind:N"),
                alt.Tooltip("share:Q", title="Share", format=".1f"),
                alt.Tooltip("n:Q", title="Count"),
            ],
        ).properties(height=280)
        st.altair_chart(_dark(mix_chart), use_container_width=True, theme=None)
    else:
        st.info("Need both method predictions and recorded results to compare the mix.")

    st.divider()

    st.subheader("Method Prediction Accuracy")
    st.caption("A method call only counts as correct when the analyst also picked the right fighter.")
    method_table = []
    for method in ["KO/TKO", "Submission", "Decision", "NC", "DQ"]:
        predicted = [r for r in rows if r["method_prediction"] == method]
        if not predicted:
            continue
        mc, me = _acc(predicted, "method_correct")
        actual_ct = len({r["fight_id"] for r in rows if r["actual_method"] == method})
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

    st.divider()

    col_rd, col_wc = st.columns(2)

    # ── Finishes by round ─────────────────────────────────────────────────────
    with col_rd:
        st.subheader("Finishes by Round")
        finish_fights = [
            f for f in fights
            if f["actual_method"] in ("KO/TKO", "Submission") and f["actual_round"]
        ]
        if finish_fights:
            rd_rows = []
            for rd in [1, 2, 3, 4, 5]:
                n = sum(1 for f in finish_fights if f["actual_round"] == rd)
                if n:
                    rd_rows.append({"Round": f"R{rd}", "Finishes": n})
            df_rd = pd.DataFrame(rd_rows)
            rd_chart = alt.Chart(df_rd).mark_bar(
                color=SERIES_1, size=34,
                cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
            ).encode(
                x=alt.X("Round:N", sort=["R1", "R2", "R3", "R4", "R5"], title=None,
                        axis=alt.Axis(labelAngle=0)),
                y=alt.Y("Finishes:Q", title="Finishes"),
                tooltip=["Round:N", "Finishes:Q"],
            ).properties(height=240)
            st.altair_chart(_dark(rd_chart), use_container_width=True, theme=None)
        else:
            st.info("No finishes with round data yet.")

    # ── Finish rate by weight class ───────────────────────────────────────────
    with col_wc:
        st.subheader("Finish Rate by Weight Class")
        wc_rows = []
        for wc in sorted({f["weight_class"] for f in fights if f["weight_class"]}):
            wf = [f for f in fights if f["weight_class"] == wc and f["actual_method"] in ("KO/TKO", "Submission", "Decision")]
            if len(wf) < 3:
                continue
            fin = sum(1 for f in wf if f["actual_method"] in ("KO/TKO", "Submission"))
            wc_rows.append({
                "Weight Class": wc,
                "rate": fin / len(wf) * 100,
                "record": f"{fin}/{len(wf)} fights",
            })
        if wc_rows:
            df_wc = pd.DataFrame(wc_rows).sort_values("rate", ascending=False)
            wc_chart = alt.Chart(df_wc).mark_bar(
                color=SERIES_1, size=18,
                cornerRadiusTopRight=4, cornerRadiusBottomRight=4,
            ).encode(
                y=alt.Y("Weight Class:N", sort="-x", title=None),
                x=alt.X("rate:Q", title="Finish rate (%)", scale=alt.Scale(domain=[0, 100])),
                tooltip=[
                    alt.Tooltip("Weight Class:N"),
                    alt.Tooltip("rate:Q", title="Finish rate", format=".1f"),
                    alt.Tooltip("record:N", title="Finishes"),
                ],
            ).properties(height=240)
            st.altair_chart(_dark(wc_chart), use_container_width=True, theme=None)
        else:
            st.info("Need at least 3 results in a weight class to chart finish rates.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 · Event Breakdown
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
        ev_fights = _fight_summaries(ev_all)
        ev_busts = sum(
            1 for f in ev_fights
            if f["consensus_correct"] is False and (f["consensus_share"] or 0) >= 70
        )
        ek1, ek2, ek3, ek4 = st.columns(4)
        ek1.metric("Fights on card", len(fight_groups))
        ek2.metric("Picks evaluated", len(ev_evaluated))
        ek3.metric("Accuracy", _pct(len(ev_correct), len(ev_evaluated)))
        ek4.metric("Consensus busts", ev_busts, help="Fights where ≥70% of analysts backed the loser.")

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

            picks_a = sum(1 for r in fr if r["picked_side"] == "a")
            picks_b = sum(1 for r in fr if r["picked_side"] == "b")
            total_picks = picks_a + picks_b

            if total_picks > 0:
                consensus_fighter = fa if picks_a > picks_b else (fb if picks_b > picks_a else None)
            else:
                consensus_fighter = None
            consensus_share = max(picks_a, picks_b) / total_picks * 100 if total_picks else 0

            is_nc = winner.lower() in ("nc / draw", "nc", "draw") if winner else False

            fa_norm = _norm(fa)
            fb_norm = _norm(fb)
            upset = False
            if has_result and winner and not is_nc:
                winner_side = _resolve_side(_norm(winner), fa_norm, fb_norm)
                consensus_side = "a" if consensus_fighter == fa else ("b" if consensus_fighter == fb else None)
                consensus_hit = winner_side is not None and consensus_side is not None and winner_side == consensus_side
                result_icon = "✅" if consensus_hit else "❌"
                upset = (not consensus_hit) and consensus_side is not None and consensus_share >= 70
            elif has_result and is_nc:
                result_icon = "🔄"
            else:
                result_icon = "⬜"

            bout_str = f"  ·  bout #{fr[0]['bout_order']}" if fr[0].get("bout_order") else ""
            title_str = "  🏆" if title else ""
            upset_str = "  🔥 UPSET" if upset else ""
            header = f"{result_icon}  **{fa} vs {fb}**  ·  {wc}{bout_str}{title_str}{upset_str}"
            if has_result and winner:
                method_str = f" via {act_method}" if act_method else ""
                header += f"  —  **{winner}**{method_str}"

            with st.expander(header, expanded=False):
                a_odds = fr[0]["a_odds"]
                b_odds = fr[0]["b_odds"]
                if a_odds is not None or b_odds is not None:
                    st.caption(f"Odds:  {fa} {_fmt_odds(a_odds)}  |  {fb} {_fmt_odds(b_odds)}")

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
# TAB 5 · Officials
# ─────────────────────────────────────────────────────────────────────────────

with tab_off:
    st.subheader("Officials")
    st.caption(
        "Referees and judges across all recorded results. "
        "An official can appear in both roles (e.g. a referee who also judged a fight)."
    )

    fight_map_off = {f["fight_id"]: f for f in raw["fights"]}
    event_map_off = {e["event_id"]: e for e in raw["events"]}

    judges: dict[str, list] = {}
    referees: dict[str, list] = {}

    for res in raw["results"]:
        fight = fight_map_off.get(res["fight_id"], {})
        event = event_map_off.get(fight.get("event_id"), {})
        fight_label = f"{fight.get('fighter_a', '')} vs {fight.get('fighter_b', '')}"
        event_label = event.get("name", "")
        event_date = event.get("date", "") or ""
        actual_winner = (res.get("winner") or "").strip()
        method = (res.get("method") or "").strip()
        rnd = res.get("round")

        bout_order = fight.get("bout_order")

        ref = (res.get("referee") or "").strip()
        if ref:
            if ref not in referees:
                referees[ref] = []
            referees[ref].append({
                "event": event_label,
                "event_date": event_date,
                "bout_order": bout_order,
                "fight": fight_label,
                "winner": actual_winner,
                "method": method,
                "round": rnd,
            })

        for j in [1, 2, 3]:
            jname = (res.get(f"judge{j}_name") or "").strip()
            jscore = (res.get(f"judge{j}_score") or "").strip()
            jwinner = (res.get(f"judge{j}_winner") or "").strip()
            if jname:
                if jname not in judges:
                    judges[jname] = []
                judges[jname].append({
                    "event": event_label,
                    "event_date": event_date,
                    "bout_order": bout_order,
                    "fight": fight_label,
                    "score": jscore,
                    "scored_for": jwinner,
                    "actual_winner": actual_winner,
                    "method": method,
                })

    if not judges and not referees:
        st.info("No officials recorded yet. Enter referee and judge data on the **Results Entry** page.")
    else:
        # ── Judges ──────────────────────────────────────────────────────────
        if judges:
            st.markdown("### Judges")

            judge_summary = []
            for name, jfights in sorted(judges.items()):
                scores = [f["score"] for f in jfights if f["score"]]
                most_common_score = max(set(scores), key=scores.count) if scores else "—"

                scoreable = [
                    f for f in jfights
                    if f["scored_for"] and f["actual_winner"]
                    and f["actual_winner"] not in ("", "Draw", "NC", "NC / Draw")
                ]
                correct = sum(1 for f in scoreable if f["scored_for"] == f["actual_winner"])
                accuracy = f"{correct}/{len(scoreable)} ({correct/len(scoreable):.0%})" if scoreable else "—"

                judge_summary.append({
                    "Judge": name,
                    "Fights Judged": len(jfights),
                    "Most Common Score": most_common_score,
                    "Scorecard Accuracy": accuracy,
                })
            judge_summary.sort(key=lambda x: x["Fights Judged"], reverse=True)
            st.dataframe(pd.DataFrame(judge_summary), use_container_width=True, hide_index=True)

            st.markdown("**Detail by Judge**")
            for name, jfights in sorted(judges.items(), key=lambda x: -len(x[1])):
                with st.expander(f"**{name}** · {len(jfights)} fight(s)", expanded=False):
                    detail_rows = []
                    for f in sorted(jfights, key=lambda x: (x["event_date"] or "", -(x["bout_order"] if x["bout_order"] is not None else 999)), reverse=True):
                        if f["scored_for"] and f["actual_winner"] and f["actual_winner"] not in ("", "Draw", "NC", "NC / Draw"):
                            correct_icon = "✓" if f["scored_for"] == f["actual_winner"] else "✗"
                        else:
                            correct_icon = "—"
                        detail_rows.append({
                            "Event": f["event"],
                            "Fight": f["fight"],
                            "Score": f["score"] or "—",
                            "Scored For": f["scored_for"] or "—",
                            "Actual Winner": f["actual_winner"] or "—",
                            "Correct": correct_icon,
                        })
                    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

        # ── Referees ─────────────────────────────────────────────────────────
        if referees:
            if judges:
                st.divider()
            st.markdown("### Referees")

            ref_summary = []
            for name, rfights in sorted(referees.items()):
                ko_tko = sum(1 for f in rfights if f["method"] == "KO/TKO")
                sub = sum(1 for f in rfights if f["method"] == "Submission")
                dec = sum(1 for f in rfights if f["method"] == "Decision")
                other = sum(1 for f in rfights if f["method"] not in ("KO/TKO", "Submission", "Decision") and f["method"])
                ref_summary.append({
                    "Referee": name,
                    "Fights": len(rfights),
                    "KO/TKO": ko_tko,
                    "Submission": sub,
                    "Decision": dec,
                    "NC/DQ": other,
                })
            ref_summary.sort(key=lambda x: x["Fights"], reverse=True)
            st.dataframe(pd.DataFrame(ref_summary), use_container_width=True, hide_index=True)

            st.markdown("**Detail by Referee**")
            for name, rfights in sorted(referees.items(), key=lambda x: -len(x[1])):
                with st.expander(f"**{name}** · {len(rfights)} fight(s)", expanded=False):
                    detail_rows = []
                    for f in sorted(rfights, key=lambda x: (x["event_date"] or "", -(x["bout_order"] if x["bout_order"] is not None else 999)), reverse=True):
                        detail_rows.append({
                            "Event": f["event"],
                            "Fight": f["fight"],
                            "Winner": f["winner"] or "—",
                            "Method": f["method"] or "—",
                            "Round": f["round"] if f["round"] else "—",
                        })
                    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
