"""Streamlit-based GrimoireLab configuration UI.

Launch via ``open-pulse grimoire ui``.  The app is password-protected:
set the ``GRIMOIRE_UI_PASSWORD`` environment variable **or** add it to
``.streamlit/secrets.toml`` as ``password = "..."``.

Requires the ``grimoire-ui`` optional dependency group::

    pip install open-pulse[grimoire-ui]
"""

from __future__ import annotations

import os
import sys


def _check_password() -> bool:
    """Prompt for a password and validate it against the configured secret."""
    import streamlit as st

    expected = os.environ.get("GRIMOIRE_UI_PASSWORD") or st.secrets.get("password", "")
    if not expected:
        st.warning(
            "No password configured.  Set `GRIMOIRE_UI_PASSWORD` env var "
            "or add `password` to `.streamlit/secrets.toml`."
        )
        return True

    entered = st.text_input("Password", type="password")
    if not entered:
        st.info("Enter the password to continue.")
        st.stop()
    if entered != expected:
        st.error("Incorrect password.")
        st.stop()
    return True


def main() -> None:
    """Entry point rendered by Streamlit."""
    import streamlit as st

    st.set_page_config(page_title="GrimoireLab Config", layout="wide")
    _check_password()

    st.title("GrimoireLab Configuration Builder")
    st.markdown(
        "Use this interface to select repositories discovered in the "
        "knowledge graph and generate a GrimoireLab `projects.json` file."
    )

    st.subheader("1. Data source")
    neo4j_endpoint = st.text_input("Neo4j endpoint", value="bolt://localhost:7687")
    tentris_endpoint = st.text_input(
        "Tentris SPARQL endpoint", value="http://localhost:7502/sparql"
    )

    st.subheader("2. Repository selection")
    st.info(
        "Repository discovery is a placeholder.  "
        "Connect a live SPARQL backend to populate this list."
    )

    if st.button("Generate config"):
        from open_pulse.grimoire.sparql_config import generate_config

        path = generate_config(
            neo4j_endpoint=neo4j_endpoint,
            tentris_endpoint=tentris_endpoint,
        )
        st.success(f"Configuration written to `{path}`.")


def launch_streamlit() -> None:
    """Spawn a ``streamlit run`` subprocess pointing at this module."""
    import subprocess

    module_path = os.path.abspath(__file__)
    cmd = [sys.executable, "-m", "streamlit", "run", module_path]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
