import hmac

import streamlit as st

st.set_page_config(
    page_title="ChatMMAPicks",
    page_icon=None,
    layout="wide",
)


# ── Authentication gate ───────────────────────────────────────────────────────
# Runs before navigation, so every page is protected. Requires `app_password`
# in Streamlit secrets (Settings → Secrets on Streamlit Cloud):
#
#   app_password = "your-strong-password-here"
#
def _check_password() -> bool:
    """Return True once the user has entered the correct password this session."""
    if st.session_state.get("authed"):
        return True

    if "app_password" not in st.secrets:
        st.error(
            "No `app_password` found in Streamlit secrets. "
            "Add one in **Settings → Secrets** before using this app — "
            "it is publicly reachable without it."
        )
        return False

    st.title("ChatMMAPicks")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary")

    if submitted:
        if hmac.compare_digest(pw, st.secrets["app_password"]):
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not _check_password():
    st.stop()


pages = [
    st.Page("pages/1_url_ingestion.py",  title="URL Ingestion",  icon=None),
    st.Page("pages/2_qc_editor.py",      title="QC / Editor",    icon=None),
    st.Page("pages/3_results_entry.py",  title="Results Entry",  icon=None),
    st.Page("pages/4_analytics.py",      title="Analytics",      icon=None),
    st.Page("pages/5_export.py",         title="Export",         icon=None),
    st.Page("pages/6_chat.py",           title="Chat",           icon=None),
]

pg = st.navigation(pages)
pg.run()
