"""
Results Entry — record the official outcome of each fight.

Select an event, then enter (or edit) the winner, method, round, time,
referee, and judges for each fight.  Results unlock Phase 5 analytics.
"""

import streamlit as st

from utils.db import (
    delete_result,
    get_events,
    get_fights_with_results_for_event,
    upsert_result,
)

# ── constants ──────────────────────────────────────────────────────────────────

METHOD_OPTIONS = ["", "KO/TKO", "Submission", "Decision", "NC", "DQ"]

# ── helpers ────────────────────────────────────────────────────────────────────


def _fighter_options(fight: dict) -> list[str]:
    """Return [fighter_a, fighter_b] plus 'NC / Draw' as pick choices."""
    return [fight["fighter_a"], fight["fighter_b"], "NC / Draw"]


def _result_summary(result: dict) -> str:
    parts = []
    if result.get("winner"):
        parts.append(f"**{result['winner']}**")
    if result.get("method"):
        parts.append(result["method"])
    if result.get("round"):
        parts.append(f"R{result['round']}")
    if result.get("time"):
        parts.append(result["time"])
    return "  ·  ".join(parts) if parts else "(no result recorded)"


def _render_fight_result(fight: dict) -> None:
    """Render a fight card with result entry form."""
    fight_id = fight["fight_id"]
    result = fight.get("result")

    bout_label = f"  ·  bout #{fight['bout_order']}" if fight.get("bout_order") else ""
    has_result = result is not None
    status_icon = "✅" if has_result else "⬜"
    expander_label = (
        f"{status_icon}  {fight['fighter_a']} vs {fight['fighter_b']}"
        f"  ·  {fight.get('weight_class') or '?'}"
        + bout_label
    )
    if has_result:
        expander_label += f"  —  {_result_summary(result)}"

    with st.expander(expander_label, expanded=not has_result):

        fighter_opts = _fighter_options(fight)

        # Pre-fill from existing result
        cur_winner = result.get("winner") if result else None
        cur_method = result.get("method") if result else None
        cur_round = result.get("round") if result else None
        cur_time = result.get("time") if result else ""
        cur_referee = result.get("referee") if result else ""
        cur_finish_details = result.get("finish_details") if result else ""
        cur_j1_name = result.get("judge1_name") if result else ""
        cur_j1_score = result.get("judge1_score") if result else ""
        cur_j1_winner = result.get("judge1_winner") if result else None
        cur_j2_name = result.get("judge2_name") if result else ""
        cur_j2_score = result.get("judge2_score") if result else ""
        cur_j2_winner = result.get("judge2_winner") if result else None
        cur_j3_name = result.get("judge3_name") if result else ""
        cur_j3_score = result.get("judge3_score") if result else ""
        cur_j3_winner = result.get("judge3_winner") if result else None

        # Winner / method / round / time
        row1 = st.columns([2, 1, 1, 1])
        with row1[0]:
            winner_idx = fighter_opts.index(cur_winner) if cur_winner in fighter_opts else 0
            r_winner = st.selectbox(
                "Winner",
                options=fighter_opts,
                index=winner_idx,
                key=f"re_win_{fight_id}",
            )
        with row1[1]:
            method_idx = METHOD_OPTIONS.index(cur_method) if cur_method in METHOD_OPTIONS else 0
            r_method = st.selectbox(
                "Method",
                options=METHOD_OPTIONS,
                index=method_idx,
                key=f"re_met_{fight_id}",
            )
        with row1[2]:
            r_round = st.number_input(
                "Round",
                min_value=1,
                max_value=5,
                step=1,
                value=int(cur_round) if cur_round else None,
                key=f"re_rnd_{fight_id}",
            )
        with row1[3]:
            r_time = st.text_input(
                "Time (e.g. 4:32)",
                value=cur_time or "",
                key=f"re_tim_{fight_id}",
            )

        # Referee
        r_referee = st.text_input(
            "Referee",
            value=cur_referee or "",
            key=f"re_ref_{fight_id}",
        )

        # Finish details (KO/TKO, Submission, NC, DQ only)
        if r_method in ("KO/TKO", "Submission", "NC", "DQ"):
            r_finish_details = st.text_input(
                "Details (e.g. Punches to Head at Distance, Rear Naked Choke)",
                value=cur_finish_details or "",
                key=f"re_det_{fight_id}",
            )
        else:
            r_finish_details = None

        # Judges scorecards (only shown if method is Decision)
        if r_method == "Decision":
            st.markdown("**Judges' Scorecards**")
            judge_winner_opts = ["", fight["fighter_a"], fight["fighter_b"]]

            # Column headers
            hcols = st.columns([3, 2, 3])
            hcols[0].markdown("**Judge**")
            hcols[1].markdown("**Score**")
            hcols[2].markdown("**Winner**")

            # Judge 1
            j1cols = st.columns([3, 2, 3])
            with j1cols[0]:
                r_j1_name = st.text_input(
                    "Judge 1 name", value=cur_j1_name or "",
                    label_visibility="collapsed", key=f"re_j1n_{fight_id}",
                    placeholder="Judge name",
                )
            with j1cols[1]:
                r_j1_score = st.text_input(
                    "Judge 1 score", value=cur_j1_score or "",
                    label_visibility="collapsed", key=f"re_j1s_{fight_id}",
                    placeholder="e.g. 48-47",
                )
            with j1cols[2]:
                j1w_idx = judge_winner_opts.index(cur_j1_winner) if cur_j1_winner in judge_winner_opts else 0
                r_j1_winner = st.selectbox(
                    "Judge 1 winner", options=judge_winner_opts, index=j1w_idx,
                    label_visibility="collapsed", key=f"re_j1w_{fight_id}",
                )

            # Judge 2
            j2cols = st.columns([3, 2, 3])
            with j2cols[0]:
                r_j2_name = st.text_input(
                    "Judge 2 name", value=cur_j2_name or "",
                    label_visibility="collapsed", key=f"re_j2n_{fight_id}",
                    placeholder="Judge name",
                )
            with j2cols[1]:
                r_j2_score = st.text_input(
                    "Judge 2 score", value=cur_j2_score or "",
                    label_visibility="collapsed", key=f"re_j2s_{fight_id}",
                    placeholder="e.g. 48-47",
                )
            with j2cols[2]:
                j2w_idx = judge_winner_opts.index(cur_j2_winner) if cur_j2_winner in judge_winner_opts else 0
                r_j2_winner = st.selectbox(
                    "Judge 2 winner", options=judge_winner_opts, index=j2w_idx,
                    label_visibility="collapsed", key=f"re_j2w_{fight_id}",
                )

            # Judge 3
            j3cols = st.columns([3, 2, 3])
            with j3cols[0]:
                r_j3_name = st.text_input(
                    "Judge 3 name", value=cur_j3_name or "",
                    label_visibility="collapsed", key=f"re_j3n_{fight_id}",
                    placeholder="Judge name",
                )
            with j3cols[1]:
                r_j3_score = st.text_input(
                    "Judge 3 score", value=cur_j3_score or "",
                    label_visibility="collapsed", key=f"re_j3s_{fight_id}",
                    placeholder="e.g. 48-47",
                )
            with j3cols[2]:
                j3w_idx = judge_winner_opts.index(cur_j3_winner) if cur_j3_winner in judge_winner_opts else 0
                r_j3_winner = st.selectbox(
                    "Judge 3 winner", options=judge_winner_opts, index=j3w_idx,
                    label_visibility="collapsed", key=f"re_j3w_{fight_id}",
                )
        else:
            r_j1_name = r_j1_score = r_j1_winner = None
            r_j2_name = r_j2_score = r_j2_winner = None
            r_j3_name = r_j3_score = r_j3_winner = None

        # Save / clear buttons
        btn1, btn2 = st.columns([1, 4])
        with btn1:
            if st.button("Save result", key=f"re_save_{fight_id}", type="primary"):
                if not r_winner:
                    st.error("Select a winner (or NC / Draw).")
                elif not r_method:
                    st.error("Select a method.")
                else:
                    try:
                        upsert_result(
                            fight_id=fight_id,
                            winner=r_winner,
                            method=r_method,
                            round_num=int(r_round) if r_round is not None else None,
                            time=r_time.strip() or None,
                            referee=r_referee.strip() or None,
                            finish_details=r_finish_details.strip() if r_finish_details else None,
                            judge1_name=r_j1_name.strip() if r_j1_name else None,
                            judge1_score=r_j1_score.strip() if r_j1_score else None,
                            judge1_winner=r_j1_winner or None,
                            judge2_name=r_j2_name.strip() if r_j2_name else None,
                            judge2_score=r_j2_score.strip() if r_j2_score else None,
                            judge2_winner=r_j2_winner or None,
                            judge3_name=r_j3_name.strip() if r_j3_name else None,
                            judge3_score=r_j3_score.strip() if r_j3_score else None,
                            judge3_winner=r_j3_winner or None,
                        )
                        st.success("Result saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Error saving result: {exc}")

        with btn2:
            if has_result:
                result_id = result["result_id"]
                confirm_key = f"re_confirm_clear_{fight_id}"
                if not st.session_state.get(confirm_key, False):
                    if st.button("Clear result", key=f"re_clear_{fight_id}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning("Remove this result permanently?")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Confirm clear", key=f"re_confirm_clear_btn_{fight_id}", type="primary"):
                            delete_result(result_id)
                            del st.session_state[confirm_key]
                            st.rerun()
                    with c2:
                        if st.button("Cancel", key=f"re_cancel_clear_{fight_id}"):
                            del st.session_state[confirm_key]
                            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Page layout
# ═══════════════════════════════════════════════════════════════════════════════

st.title("Results Entry")
st.caption("Record official fight outcomes to unlock analytics.")

events = get_events()

if not events:
    st.info("No events in the database yet. Use the **URL Ingestion** page to add predictions.")
    st.stop()


def _event_label(e: dict) -> str:
    date_str = e.get("date") or "(no date)"
    return f"{e['name']}  –  {date_str}"


event_ids = [e["event_id"] for e in events]
selected_event_id = st.selectbox(
    "Select event",
    options=event_ids,
    format_func=lambda eid: _event_label(next(e for e in events if e["event_id"] == eid)),
    key="re_selected_event_id",
)

fights = get_fights_with_results_for_event(selected_event_id)

if not fights:
    st.info("No fights found for this event. Add predictions via **URL Ingestion** first.")
    st.stop()

total = len(fights)
done = sum(1 for f in fights if f.get("result") is not None)
st.progress(done / total, text=f"{done} / {total} fights with results entered")

st.divider()

for fight in fights:
    _render_fight_result(fight)
