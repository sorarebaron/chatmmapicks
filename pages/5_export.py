import csv
import io

import streamlit as st

from utils.db import get_events, get_picks_for_event, get_all_picks

CHATMMA_COLUMNS = ["date", "analyst", "platform", "event", "location", "fight", "weight_class", "pick", "context", "method"]

st.title("Export Picks")
st.caption("Download picks as a CSV compatible with the original ChatMMA app.")

events = get_events()

if not events:
    st.info("No events found. Ingest some articles first.")
    st.stop()

event_options = {f"{e['name']} ({e['date'] or 'no date'})": e["event_id"] for e in events}
selected_label = st.selectbox("Select event", list(event_options.keys()))
selected_event_id = event_options[selected_label]

rows = get_picks_for_event(selected_event_id)

if not rows:
    st.warning("No picks found for this event yet.")
    st.stop()

st.success(f"**{len(rows)}** pick(s) found.")

# ── Preview table ─────────────────────────────────────────────────────────────
with st.expander("Preview", expanded=True):
    st.dataframe(
        [{col: r[col] for col in CHATMMA_COLUMNS} for r in rows],
        use_container_width=True,
    )

st.divider()

# ── CSV download ──────────────────────────────────────────────────────────────
buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=CHATMMA_COLUMNS, extrasaction="ignore")
writer.writeheader()
writer.writerows(rows)
csv_bytes = buf.getvalue().encode("utf-8")

event_name_slug = selected_label.split(" (")[0].replace(" ", "_").lower()
st.download_button(
    label="⬇️ Download CSV (ChatMMA format)",
    data=csv_bytes,
    file_name=f"{event_name_slug}_picks.csv",
    mime="text/csv",
    type="primary",
)

st.caption(
    "Columns: date · analyst · platform · event · location · fight · weight_class · pick · context · method"
)

st.divider()

# ── Export all events ──────────────────────────────────────────────────────────
st.subheader("Export All Events")
st.caption("Download every pick across all events in a single CSV.")

all_rows = get_all_picks()
if all_rows:
    all_buf = io.StringIO()
    all_writer = csv.DictWriter(all_buf, fieldnames=CHATMMA_COLUMNS, extrasaction="ignore")
    all_writer.writeheader()
    all_writer.writerows(all_rows)
    all_csv_bytes = all_buf.getvalue().encode("utf-8")

    st.download_button(
        label="⬇️ Download all events CSV",
        data=all_csv_bytes,
        file_name="all_events_picks.csv",
        mime="text/csv",
        type="primary",
    )
    st.caption(f"{len(all_rows)} total pick(s) across all events.")
else:
    st.info("No picks found in the database.")
