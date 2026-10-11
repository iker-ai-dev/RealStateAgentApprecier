"""Public MCP connection surface, shared by both Streamlit entrypoints."""

from html import escape
from pathlib import Path

import streamlit as st

from app.mcp_install import PUBLIC_MCP_URL, remote_install_commands


# The keyed container is also the lifetime boundary for the page's CSS.
CSS = """
<style>
.stApp:has(.st-key-mcp_landing) { background:#111615; color:#f5f7f6; }
.stApp:has(.st-key-mcp_landing) [data-testid="stHeader"] { background:#111615; }
.stApp:has(.st-key-mcp_landing) .block-container {
  max-width:1240px; padding-top:2.8rem; padding-bottom:3rem;
}
.st-key-mcp_landing { font-family:inherit; }
.st-key-mcp_landing * { letter-spacing:0; }
.st-key-mcp_landing h1, .st-key-mcp_landing h2, .st-key-mcp_landing h3 { color:#f5f7f6; }
.st-key-mcp_landing h2 { font-size:1.3rem !important; padding-top:0; }
.st-key-mcp_landing p { color:#bdc9c3; }
.st-key-mcp_landing a { color:#79e5c6; text-decoration:none; }
.st-key-mcp_landing a:hover { text-decoration:underline; }
.st-key-mcp_landing a:focus-visible { outline:2px solid #79e5c6; outline-offset:5px; }
.mcp-top { display:flex; align-items:center; justify-content:space-between; gap:20px;
  padding-bottom:24px; border-bottom:1px solid #34413b; }
.mcp-brand { display:flex; align-items:center; gap:12px; font-weight:650; color:#f5f7f6; }
.mcp-mark { display:grid; place-items:center; width:38px; height:38px; border:1px solid #79e5c6;
  border-radius:6px; color:#79e5c6; font:600 13px monospace; }
.mcp-top a { font-size:14px; }
.mcp-intro { padding:34px 0 24px; }
.mcp-kicker { display:flex; align-items:center; gap:8px; color:#79e5c6; font:600 12px monospace; }
.mcp-kicker::before { content:""; width:7px; height:7px; background:#79e5c6; }
.mcp-intro h1 { font-size:48px; line-height:1.12; margin:15px 0 14px; padding:0; font-weight:650; }
.mcp-intro p { font-size:18px; line-height:1.6; max-width:690px; margin:0; }
.mcp-specs { display:flex; flex-wrap:wrap; gap:12px 32px; padding:15px 0 20px;
  border-bottom:1px solid #34413b; font:12px monospace; color:#a9b9b0; }
.mcp-specs b { color:#f5f7f6; font-weight:500; }
.mcp-section { display:flex; align-items:center; gap:12px; margin:20px 0 12px; }
.mcp-section > span { color:#79e5c6; font:12px monospace; }
.mcp-section h2 { margin:0; padding:0; }
.st-key-mcp_landing [data-baseweb="tab-list"] { background:transparent; gap:16px; }
.st-key-mcp_landing [data-baseweb="tab"] { color:#afbbb4; font-size:14px; padding:8px 0; }
.st-key-mcp_landing [data-baseweb="tab"] p { color:inherit; font-size:14px; }
.st-key-mcp_landing [data-baseweb="tab"][aria-selected="true"] { color:#79e5c6; }
.st-key-mcp_landing [data-baseweb="tab-highlight"] { background:#79e5c6; }
.st-key-mcp_landing [data-baseweb="tab-border"] { background:#34413b; }
.st-key-mcp_landing [data-testid="stCode"] { border:1px solid #3e5047; border-radius:6px; }
.st-key-mcp_landing [data-testid="stCode"] pre { background:#1b2520 !important; color:#e9f7ef !important;
  font-size:13px; line-height:1.7; min-height:76px; padding-right:3rem; }
.st-key-mcp_landing [data-testid="stCode"] code { color:#e9f7ef !important; }
.st-key-mcp_landing [data-testid="stCode"] button { color:#f5f7f6; background:#304138; }
.st-key-mcp_landing [data-testid="stCode"] > div:has([data-testid="stElementToolbarButton"]) {
  opacity:1 !important; visibility:visible !important;
}
.st-key-mcp_landing [data-testid="stCode"] [data-testid="stElementToolbarButton"],
.st-key-mcp_landing [data-testid="stCode"] button { visibility:visible !important; }
.st-key-mcp_landing [data-testid="stCaptionContainer"] p { color:#aebdb5 !important; font-size:13px; }
.mcp-label { color:#aebdb5; font:12px monospace; margin:18px 0 8px; }
.mcp-tool { display:grid; grid-template-columns:30px minmax(0,1fr); gap:10px;
  border-top:1px solid #34413b; padding:16px 0; }
.mcp-tool .mcp-index { color:#78a08d; font:12px monospace; padding-top:4px; }
.mcp-tool code { display:block; color:#edf7f1; background:none; padding:0;
  font-size:14px; overflow-wrap:anywhere; white-space:normal; }
.mcp-tool p { font-size:14px; margin:5px 0 0; line-height:1.5; }
.mcp-boundary { border-left:2px solid #ff9584; padding:2px 0 2px 16px; margin:20px 0 8px; }
.mcp-boundary strong { color:#ffad9f; font-size:13px; }
.mcp-boundary p { font-size:13px; line-height:1.65; margin:6px 0 0; }
.mcp-preview-heading { display:flex; justify-content:space-between; flex-wrap:wrap; gap:12px;
  align-items:center; border-top:1px solid #34413b; padding:24px 0 4px; margin-top:20px; }
.mcp-preview-heading h2 { margin:0; padding:0; }
.mcp-preview-heading > span { color:#aebdb5; font:12px monospace; }
.st-key-mcp_landing [data-testid="stImage"] img { border:1px solid #34413b; border-radius:6px; }
.mcp-footer { display:flex; justify-content:space-between; flex-wrap:wrap; gap:12px;
  border-top:1px solid #34413b; padding-top:20px; margin-top:18px; font-size:12px; color:#aebdb5; }
@media(max-width:700px) {
  .stApp:has(.st-key-mcp_landing) .block-container { padding:3.5rem 1rem 2rem; }
  .mcp-top { gap:12px; padding-bottom:18px; }
  .mcp-top a { font-size:12px; }
  .mcp-brand { font-size:14px; gap:8px; }
  .mcp-intro { padding-top:26px; }
  .mcp-intro h1 { font-size:36px; }
  .mcp-intro p { font-size:16px; }
  .mcp-specs { gap:12px 20px; }
  .st-key-mcp_landing [data-testid="stHorizontalBlock"] { flex-wrap:wrap; }
  .st-key-mcp_landing [data-testid="stColumn"] { width:100% !important; flex:1 1 100% !important; min-width:0; }
  .st-key-mcp_landing [data-baseweb="tab-list"] { gap:14px; overflow-x:auto; }
  .st-key-mcp_landing [data-baseweb="tab"] { font-size:12px; flex-shrink:0; }
  .st-key-mcp_landing [data-baseweb="tab"] p { font-size:12px; }
}
</style>
"""

TOOLS = (
    ("list_shopping_centers", "Shopping-center inventory and core acquisition metrics."),
    ("get_shopping_center_context", "Property, rent roll, market, comps and valuation context."),
    ("appraise_shopping_center", "Illustrative cap-rate, sales-comparison and DCF estimates."),
    ("ask_retailscope", "Deterministic underwriting answers for a selected center."),
)


def render_mcp_landing() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    with st.container(key="mcp_landing"):
        st.markdown("""
<div class="mcp-top">
  <div class="mcp-brand"><span class="mcp-mark">RS</span> RetailScope AI</div>
  <a href="/" target="_self">Open workspace &#8599;</a>
</div>
<div class="mcp-intro">
  <div class="mcp-kicker">PUBLIC MCP / REMOTE ACCESS</div>
  <h1>RetailScope AI</h1>
  <p>Shopping-center underwriting, connected to your agent.
  Rent rolls, market context and illustrative valuations through one public MCP endpoint.</p>
</div>
<div class="mcp-specs">
  <span><b>04</b> underwriting tools</span>
  <span><b>HTTP</b> remote transport</span>
  <span><b>READ-ONLY</b> public demo</span>
</div>
""", unsafe_allow_html=True)
        connection, tools = st.columns([1.25, 1], gap="large")
        with connection:
            st.markdown('<div class="mcp-section"><span>01 /</span><h2>Connect your agent</h2></div>', unsafe_allow_html=True)
            commands = remote_install_commands()
            for tab, (label, command) in zip(st.tabs(list(commands)), commands.items()):
                with tab:
                    st.code(command, language="json" if label in {"OpenCode", "Generic JSON"} else "bash", wrap_lines=True)
            st.markdown('<div class="mcp-label">PUBLIC ENDPOINT</div>', unsafe_allow_html=True)
            st.code(PUBLIC_MCP_URL, language="text", wrap_lines=True)
            st.caption("Free demo hosting may take a moment to wake.")
            st.markdown('<div class="mcp-label">SC-001 / EXAMPLE QUERY</div>', unsafe_allow_html=True)
            st.code("Use RetailScope AI to appraise SC-001 and tell me whether the rent roll supports the asking price.", language="text", wrap_lines=True)
        with tools:
            st.markdown('<div class="mcp-section"><span>02 /</span><h2>Underwriting toolkit</h2></div>', unsafe_allow_html=True)
            rows = "".join(
                f'<div class="mcp-tool"><span class="mcp-index">{index:02d}</span>'
                f'<div><code>{escape(name)}</code><p>{escape(description)}</p></div></div>'
                for index, (name, description) in enumerate(TOOLS, 1)
            )
            st.markdown(rows, unsafe_allow_html=True)
            st.markdown("""
<div class="mcp-boundary">
  <strong>DEMO DATA. DEFINED LIMITS.</strong>
  <p>Public sample CSV data only. Deterministic answers; no live LLM inference
  or private Snowflake data. Inputs and model assumptions are unverified.
  Research support, not a certified appraisal or an investment recommendation.</p>
</div>
""", unsafe_allow_html=True)
        st.markdown("""
<div class="mcp-preview-heading"><h2>The underwriting workspace</h2>
  <span>WESTGATE PLAZA / SAMPLE DATA</span></div>
""", unsafe_allow_html=True)
        st.image(str(Path(__file__).parent / "assets" / "retailscope-workspace.png"),
                 caption="Actual RetailScope AI workspace capture. Illustrative values, not verified results.",
                 width="stretch")
        st.markdown("""
<div class="mcp-footer"><span>RetailScope AI / Public MCP demo</span>
  <a href="/" target="_self">Explore the workspace &#8599;</a></div>
""", unsafe_allow_html=True)
