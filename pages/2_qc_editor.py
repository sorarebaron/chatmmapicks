"""
QC / Editor — browse, edit, and manage saved picks.

Tab 1: Event & Fights
  - Select event from dropdown
  - Edit event metadata (name, date, location)
  - Per fight: edit fighter names / weight class / bout order
  - Per pick: edit all fields, delete pick
  - Delete fight (with cascade warning + confirmation)

Tab 2: Fighter Aliases
  - View, add, and delete alias mappings
"""

import datetime
import streamlit as st

from utils.db import (
    delete_alias,
    delete_fight,
    delete_pick,
    get_events,
    get_fighter_aliases,
    get_fights_for_event,
    get_picks_for_fight,
    save_alias,
    save_pick_tags,
    update_event,
    update_fight,
    update_pick,
)

# ── constants ─────────────────────────────────────────────────────────────────

METHOD_OPTIONS = ["", "KO/TKO", "Submission", "Decision", "NC", "DQ"]
CONFIDENCE_OPTIONS = ["", "lean", "confident", "lock"]


# ═══════════════════════════════════════════════════════════════════════════════
# Helper renderers
# ═══════════════════════════════════════════════════════════════════════════════

def _render_delete_pick(pick_id: str) -> None:
    """Two-step delete confirmation for a single pick."""
    confirm_key = f"qc_confirm_delete_pick_{pick_id}"
    if not st.session_state.get(confirm_key, False):
        if st.button("Delete pick", key=f"qc_del_pick_{pick_id}"):
            st.session_state[confirm_key] = True
            st.rerun()
    else:
        st.warning("Permanently delete this pick?")
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "Confirm delete",
                key=f"qc_confirm_del_pick_{pick_id}",
                type="primary",
            ):
                delete_pick(pick_id)
                del st.session_state[confirm_key]
                st.rerun()
        with c2:
            if st.button("Cancel", key=f"qc_cancel_del_pick_{pick_id}"):
                del st.session_state[confirm_key]
                st.rerun()


def _render_delete_fight(fight_id: str, pick_count: int) -> None:
    """Two-step delete confirmation for a fight (and all its picks)."""
    confirm_key = f"qc_confirm_delete_fight_{fight_id}"
    if not st.session_state.get(confirm_key, False):
        if st.button("Delete fight", key=f"qc_del_fight_{fight_id}"):
            st.session_state[confirm_key] = True
            st.rerun()
    else:
        st.warning(
            f"This will permanently delete the fight **and all {pick_count} pick(s)**. "
            "This cannot be undone."
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button(
                "Confirm delete fight",
                key=f"qc_confirm_del_fight_{fight_id}",
                type="primary",
            ):
                delete_fight(fight_id)
                del st.session_state[confirm_key]
                st.rerun()
        with c2:
            if st.button("Cancel", key=f"qc_cancel_del_fight_{fight_id}"):
                del st.session_state[confirm_key]
                st.rerun()


def _render_pick(pick: dict) -> None:
    """Render an editable pick card with save and delete controls."""
    pick_id = pick["pick_id"]

    with st.container(border=True):
        # Row 1: analyst info
        p_col1, p_col2, p_col3 = st.columns([2, 1, 3])
        with p_col1:
            p_analyst = st.text_input(
                "Analyst",
                value=pick.get("analyst_name") or "",
                key=f"qc_an_{pick_id}",
            )
        with p_col2:
            p_platform = st.text_input(
                "Platform",
                value=pick.get("platform") or "",
                key=f"qc_pl_{pick_id}",
            )
        with p_col3:
            p_url = st.text_input(
                "Source URL",
                value=pick.get("source_url") or "",
                key=f"qc_url_{pick_id}",
            )

        # Row 2: pick details
        p_col4, p_col5, p_col6 = st.columns([2, 1, 1])
        with p_col4:
            p_picked = st.text_input(
                "Picked to win",
                value=pick.get("picked_fighter") or "",
                key=f"qc_pw_{pick_id}",
            )
        with p_col5:
            raw_method = pick.get("method_prediction") or ""
            method_idx = METHOD_OPTIONS.index(raw_method) if raw_method in METHOD_OPTIONS else 0
            p_method = st.selectbox(
                "Method",
                options=METHOD_OPTIONS,
                index=method_idx,
                key=f"qc_me_{pick_id}",
            )
        with p_col6:
            raw_conf = pick.get("confidence_tag") or ""
            conf_idx = CONFIDENCE_OPTIONS.index(raw_conf) if raw_conf in CONFIDENCE_OPTIONS else 0
            p_conf = st.selectbox(
                "Confidence",
                options=CONFIDENCE_OPTIONS,
                index=conf_idx,
                key=f"qc_co_{pick_id}",
            )

        # Row 3: reasoning
        p_notes = st.text_area(
            "Reasoning",
            value=pick.get("reasoning_notes") or "",
            key=f"qc_rn_{pick_id}",
            height=80,
        )

        # Row 4: tags
        existing_tags = ", ".join(pick.get("tags") or [])
        p_tags = st.text_input(
            "Tags (comma-separated)",
            value=existing_tags,
            key=f"qc_tg_{pick_id}",
        )

        # Row 5: save / delete
        btn_col1, btn_col2 = st.columns([1, 4])
        with btn_col1:
            if st.button("Save pick", key=f"qc_save_pick_{pick_id}"):
                if not p_analyst.strip():
                    st.error("Analyst name cannot be blank.")
                elif not p_picked.strip():
                    st.error("Picked fighter cannot be blank.")
                else:
                    try:
                        update_pick(
                            pick_id,
                            p_analyst.strip(),
                            p_platform.strip() or None,
                            p_url.strip() or None,
                            p_picked.strip(),
                            p_method or None,
                            p_conf or None,
                            p_notes.strip() or None,
                        )
                        new_tags = [t.strip() for t in p_tags.split(",") if t.strip()]
                        save_pick_tags(pick_id, new_tags)
                        st.success("Pick saved.")
                        st.rerun()
                    except Exception as exc:
                        msg = str(exc).lower()
                        if "unique" in msg or "duplicate" in msg:
                            st.error(
                                "A pick for that analyst already exists on this fight. "
                                "Choose a unique analyst name."
                            )
                        else:
                            st.error(f"Error saving pick: {exc}")

        with btn_col2:
            _render_delete_pick(pick_id)


def _render_fight(fight: dict) -> None:
    """Render an expandable fight card with metadata editor and picks."""
    fight_id = fight["fight_id"]
    pick_count = fight["pick_count"]
    bout_label = f"  ·  bout #{fight['bout_order']}" if fight.get("bout_order") else ""
    expander_label = (
        f"{fight['fighter_a']} vs {fight['fighter_b']}"
        f"  ·  {fight.get('weight_class') or '?'}"
        f"  ·  {pick_count} pick{'s' if pick_count != 1 else ''}"
        + bout_label
    )

    with st.expander(expander_label, expanded=False):

        # ── Fight metadata editor ─────────────────────────────────────────────

        with st.container(border=True):
            st.markdown("**Fight details**")
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                f_fighter_a = st.text_input(
                    "Fighter A",
                    value=fight.get("fighter_a") or "",
                    key=f"qc_fa_{fight_id}",
                )
                f_weight = st.text_input(
                    "Weight class",
                    value=fight.get("weight_class") or "",
                    key=f"qc_wc_{fight_id}",
                )
            with f_col2:
                f_fighter_b = st.text_input(
                    "Fighter B",
                    value=fight.get("fighter_b") or "",
                    key=f"qc_fb_{fight_id}",
                )
                f_bout_order_raw = fight.get("bout_order")
                f_bout_order = st.number_input(
                    "Bout order",
                    min_value=1,
                    step=1,
                    value=int(f_bout_order_raw) if f_bout_order_raw is not None else None,
                    key=f"qc_bo_{fight_id}",
                )

            if st.button("Save fight", key=f"qc_save_fight_{fight_id}"):
                if not f_fighter_a.strip() or not f_fighter_b.strip():
                    st.error("Fighter names cannot be blank.")
                else:
                    update_fight(
                        fight_id,
                        f_fighter_a.strip(),
                        f_fighter_b.strip(),
                        f_weight.strip() or None,
                        int(f_bout_order) if f_bout_order is not None else None,
                    )
                    st.success("Fight saved.")
                    st.rerun()

        # ── Picks for this fight ──────────────────────────────────────────────

        picks = get_picks_for_fight(fight_id)

        if picks:
            st.markdown(f"**{len(picks)} pick(s)**")
            for pick in picks:
                _render_pick(pick)
        else:
            st.info("No picks for this fight.")

        # ── Delete fight ──────────────────────────────────────────────────────

        st.divider()
        _render_delete_fight(fight_id, pick_count)


def _render_aliases_tab() -> None:
    """Render the Fighter Aliases tab."""
    aliases = get_fighter_aliases()

    st.markdown(f"**{len(aliases)} alias(es) on file**")

    if aliases:
        h1, h2, h3 = st.columns([3, 3, 1])
        with h1:
            st.markdown("**Canonical name**")
        with h2:
            st.markdown("**Alias**")
        with h3:
            st.markdown("**Action**")
        st.divider()

        for alias_row in aliases:
            alias_id = alias_row["alias_id"]
            a1, a2, a3 = st.columns([3, 3, 1])
            with a1:
                st.write(alias_row.get("canonical_name") or "—")
            with a2:
                st.write(alias_row.get("alias") or "—")
            with a3:
                confirm_key = f"qc_confirm_del_alias_{alias_id}"
                if not st.session_state.get(confirm_key, False):
                    if st.button("Delete", key=f"qc_del_alias_{alias_id}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    if st.button(
                        "Confirm",
                        key=f"qc_confirm_alias_{alias_id}",
                        type="primary",
                    ):
                        delete_alias(alias_id)
                        del st.session_state[confirm_key]
                        st.rerun()
                    if st.button("Cancel", key=f"qc_cancel_alias_{alias_id}"):
                        del st.session_state[confirm_key]
                        st.rerun()
    else:
        st.info("No aliases defined yet.")

    st.divider()
    st.markdown("**Add new alias**")
    with st.container(border=True):
        al_col1, al_col2 = st.columns(2)
        with al_col1:
            new_canonical = st.text_input("Canonical name", key="qc_new_canonical")
        with al_col2:
            new_alias_val = st.text_input("Alias", key="qc_new_alias")
        if st.button("Add alias", key="qc_add_alias", type="primary"):
            if not new_canonical.strip() or not new_alias_val.strip():
                st.error("Both fields are required.")
            elif new_canonical.strip().lower() == new_alias_val.strip().lower():
                st.error("Canonical name and alias must be different.")
            else:
                try:
                    save_alias(new_canonical.strip(), new_alias_val.strip())
                    st.success(
                        f"Alias saved: **{new_alias_val.strip()}** → **{new_canonical.strip()}**"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Error saving alias: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Page layout
# ═══════════════════════════════════════════════════════════════════════════════

st.title("QC / Editor")
st.caption("Browse, edit, and manage saved picks.")

events = get_events()

if not events:
    st.info("No events in the database yet. Use the **URL Ingestion** page to add predictions.")
    st.stop()

tab1, tab2 = st.tabs(["Event & Fights", "Fighter Aliases"])

# ── Tab 1: Event & Fights ──────────────────────────────────────────────────────

with tab1:

    def _event_label(e: dict) -> str:
        date_str = e.get("date") or "(no date)"
        return f"{e['name']}  –  {date_str}"

    event_ids = [e["event_id"] for e in events]
    selected_event_id = st.selectbox(
        "Select event",
        options=event_ids,
        format_func=lambda eid: _event_label(next(e for e in events if e["event_id"] == eid)),
        key="qc_selected_event_id",
    )

    selected_event = next(e for e in events if e["event_id"] == selected_event_id)

    # Event metadata editor
    with st.container(border=True):
        st.markdown("**Event details**")
        ev_col1, ev_col2, ev_col3 = st.columns([2, 1, 2])
        with ev_col1:
            ev_name = st.text_input(
                "Event name",
                value=selected_event.get("name") or "",
                key=f"qc_ev_name_{selected_event_id}",
            )
        with ev_col2:
            raw_date = selected_event.get("date")
            parsed_date: datetime.date | None = None
            if raw_date:
                try:
                    parsed_date = datetime.date.fromisoformat(str(raw_date))
                except ValueError:
                    parsed_date = None
            ev_date = st.date_input(
                "Event date",
                value=parsed_date,
                key=f"qc_ev_date_{selected_event_id}",
                format="YYYY-MM-DD",
            )
        with ev_col3:
            ev_location = st.text_input(
                "Location",
                value=selected_event.get("location") or "",
                key=f"qc_ev_location_{selected_event_id}",
            )

        if st.button("Save event", key=f"qc_save_ev_{selected_event_id}", type="primary"):
            if not ev_name.strip():
                st.error("Event name cannot be blank.")
            else:
                date_str = ev_date.isoformat() if ev_date else None
                update_event(
                    selected_event_id,
                    ev_name.strip(),
                    date_str,
                    ev_location.strip() or None,
                )
                st.success("Event saved.")
                st.rerun()

    # Fights list
    st.divider()
    fights = get_fights_for_event(selected_event_id)

    if not fights:
        st.info("No fights found for this event.")
    else:
        st.markdown(f"**{len(fights)} fight(s)**")
        for fight in fights:
            _render_fight(fight)

# ── Tab 2: Fighter Aliases ────────────────────────────────────────────────────

with tab2:
    _render_aliases_tab()
