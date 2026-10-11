"""Shared presentation only. No research, model calls or credential access."""
import streamlit as st

CSS = """
<style>
:root {
 --rs-bg:#101214; --rs-surface:#181b1e; --rs-raised:#202428;
 --rs-border:#343a40; --rs-ink:#f3f6f5; --rs-muted:#aeb8bd;
 --rs-teal:#6ee7ce; --rs-cyan:#71d9ed; --rs-coral:#f49a87;
 --rs-navy:var(--rs-ink);
 color-scheme:dark;
}
* { letter-spacing:0 !important; }
.stApp { background:var(--rs-bg); color:var(--rs-ink); }
[data-testid="stHeader"] { background:#101214; border-bottom:1px solid #272d31; }
[data-testid="stSidebar"] { background:#16191c; border-right:1px solid var(--rs-border); }
.block-container { max-width:1360px; padding-top:4.5rem; padding-bottom:2rem; }
h1,h2,h3 { color:var(--rs-ink); letter-spacing:0; }
.stApp:not(:has(.st-key-mcp_landing)) h1 { font-size:1.85rem !important; line-height:1.2 !important; }
h2 { font-size:1.3rem !important; } h3 { font-size:1.08rem !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
 color:var(--rs-muted) !important; font-size:.8rem; line-height:1.5; opacity:1;
}
[data-testid="stMarkdownContainer"] p { line-height:1.5; }
[data-testid="stVerticalBlockBorderWrapper"]>div {
 border:1px solid var(--rs-border) !important; border-radius:7px !important;
 background:var(--rs-surface);
}
[data-testid="stMetric"] { background:transparent; border:0; padding:10px 0; min-width:0; }
[data-testid="stMetricLabel"] p { color:var(--rs-muted); font-size:.77rem; }
[data-testid="stMetricValue"] {
 color:var(--rs-ink); font-size:1.5rem; font-weight:600; white-space:normal; overflow-wrap:anywhere;
}
[data-testid="stMetricDelta"] { font-size:.72rem; white-space:normal; }
[data-testid="stButton"] button, [data-testid="stLinkButton"] a {
 border-radius:6px; min-height:40px; border:1px solid #41494f;
 background:#1b2024; color:#edf4f2; font-weight:600; transition:border-color .15s,background .15s;
}
[data-testid="stButton"] button:hover, [data-testid="stLinkButton"] a:hover {
 border-color:var(--rs-teal); background:#252d2f; color:#fff;
}
[data-testid="stButton"] button[kind="primary"] {
 background:var(--rs-teal); border-color:var(--rs-teal); color:#0b2420;
}
[data-testid="stButton"] button[kind="primary"]:hover { background:#9cf4de; color:#0b2420; }
[data-testid="stButton"] button:disabled { opacity:.5; }
button:focus-visible, a:focus-visible, input:focus-visible { outline:2px solid var(--rs-cyan) !important; outline-offset:3px; }
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea { color:var(--rs-ink); background:#202428; border-radius:6px; }
[data-baseweb="select"]>div { border-color:#41494f; border-radius:6px; background:#202428; }
[data-baseweb="popover"], [data-baseweb="menu"] { color:var(--rs-ink); }
[data-testid="stWidgetLabel"] p { color:#dce4e6; font-size:.82rem; }
[data-testid="stExpander"] {
 background:#161a1d; border:1px solid var(--rs-border); border-radius:6px; overflow:hidden;
}
[data-testid="stExpander"] details { border:0; background:transparent; border-radius:6px; }
[data-testid="stExpander"] summary { color:#e3ecea; font-size:.82rem; }
[data-testid="stExpander"] summary:hover { color:var(--rs-teal); background:#1d2426; }
[data-testid="stChatMessage"] { background:#181d21; border:1px solid var(--rs-border); border-radius:7px; }
[data-testid="stChatInput"] { border:1px solid #41494f; background:#202428; border-radius:7px; }
[data-testid="stChatInput"] textarea { color:var(--rs-ink); }
[data-testid="stBottom"]>div { background:var(--rs-bg); }
[data-testid="stAlert"] { border-radius:6px; border:1px solid #3b4e51; background:#17262b; color:#dcefee; }
[data-testid="stAlert"] p { color:#dcefee; }
[data-testid="stCode"] { background:#171c20; border:1px solid var(--rs-border); border-radius:6px; color:#dfebe9; overflow:hidden; }
[data-testid="stCode"] pre { background:transparent !important; border:0; border-radius:0; color:#dfebe9; }
[data-testid="stDataFrame"] { border:1px solid var(--rs-border); border-radius:6px; overflow:hidden; }
[data-testid="stTable"] { color:var(--rs-ink); }
[data-testid="stTable"] th { background:#202428; color:#c9d4d8; }
[data-testid="stTable"] td, [data-testid="stTable"] th { border-color:var(--rs-border); }
[data-baseweb="tab-list"] { gap:16px; background:transparent; border-bottom:1px solid var(--rs-border); }
[data-baseweb="tab"] { color:var(--rs-muted); padding:10px 0; font-size:.82rem; }
[data-baseweb="tab"][aria-selected="true"] { color:var(--rs-teal); }
[data-testid="stRadio"] [role="radiogroup"] { gap:0; border-bottom:1px solid var(--rs-border); }
[data-testid="stRadio"] [role="radiogroup"] label {
 padding:10px 16px 12px 0; margin-right:20px; border-bottom:2px solid transparent;
}
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) { border-bottom-color:var(--rs-teal); }
[data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) p { color:var(--rs-teal); }
.rs-brand { display:flex; align-items:center; gap:10px; color:var(--rs-ink); margin-bottom:2px; }
.rs-monogram {
 display:grid; place-items:center; width:34px; height:34px; flex:none;
 background:var(--rs-teal); color:#102723; border-radius:6px; font-size:.76rem; font-weight:800;
}
.rs-brand-name { font-size:1.05rem; font-weight:750; }
.rs-brand-product { font-size:.72rem; color:var(--rs-muted); margin-left:auto; }
.rs-hero {
 background:transparent; border-bottom:1px solid var(--rs-border); border-radius:0;
 padding:12px 0 18px; margin:0 0 4px; display:flex; gap:24px; align-items:center; justify-content:space-between;
}
.rs-eyebrow { color:var(--rs-teal) !important; text-transform:uppercase; font-size:.7rem; font-weight:700; margin-bottom:6px; }
.rs-hero h1 { color:var(--rs-ink); line-height:1.2; margin:0 0 8px; padding:0; }
.rs-hero p { color:var(--rs-muted); font-size:.88rem; line-height:1.5; margin:0; max-width:650px; }
.rs-mandate { min-width:190px; border-left:2px solid var(--rs-coral); padding-left:16px; }
.rs-mandate strong { display:block; color:var(--rs-ink); font-size:1.05rem; margin-bottom:4px; }
.rs-mandate span { display:block; color:var(--rs-muted); font-size:.76rem; line-height:1.6; }
.rs-tag { display:inline-block; border:1px solid #3d454a; border-radius:4px; padding:3px 7px;
 margin:0 5px 8px 0; background:#242a2f; color:#c6d1d5; font-size:.66rem; font-weight:650; }
.rs-tag-teal { border-color:#35645c; background:#1b3630; color:#92f2d9; }
.rs-tag-amber { border-color:#685538; background:#342b20; color:#f1c988; }
.rs-card-title { font-size:1.1rem; font-weight:700; color:var(--rs-ink); margin:2px 0 5px; line-height:1.3; overflow-wrap:anywhere; }
.rs-place { font-size:.79rem; color:var(--rs-muted); margin-bottom:12px; overflow-wrap:anywhere; }
.rs-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;
 border-top:1px solid var(--rs-border); border-bottom:1px solid var(--rs-border); padding:12px 0; margin-bottom:10px; }
.rs-facts span { display:block; color:var(--rs-muted); font-size:.7rem; margin-bottom:5px; }
.rs-facts strong { display:block; color:#f3f6f5; font-size:1.05rem; font-weight:650; line-height:1.3; overflow-wrap:anywhere; }
.rs-mini-heading { color:var(--rs-cyan); font-size:.67rem; font-weight:700; text-transform:uppercase; margin:8px 0 5px; }
.rs-note { color:#bcc6cb; font-size:.81rem; line-height:1.5; margin:0; }
.rs-empty { text-align:center; border:1px dashed #465258; border-radius:6px; padding:24px; margin:10px 0 16px; }
.rs-empty h3 { margin:0 0 10px; } .rs-empty p { margin:0 auto; max-width:650px; color:var(--rs-muted); line-height:1.6; }
.rs-pipeline { display:flex; gap:20px; font-size:.77rem; color:var(--rs-muted); margin:6px 0 8px; flex-wrap:wrap; }
.rs-pipeline b { color:var(--rs-teal); } .rs-pipeline span { padding:4px 0; }
.rs-workers { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px;
 background:#171d20; border-top:1px solid #3b4d4b; border-bottom:1px solid #3b4d4b; padding:16px 12px; }
.rs-workers>div { min-width:0; border-left:2px solid #365e56; padding-left:10px; }
.rs-workers strong { display:block; font-size:.79rem; font-weight:650; color:#e5eeeb; overflow-wrap:anywhere; }
.rs-workers span { display:block; font-size:.76rem; color:var(--rs-teal); margin-top:6px; }
.section-label { color:var(--rs-cyan); font-size:.7rem; font-weight:700; margin:1rem 0 .35rem; text-transform:uppercase; }
.note { color:var(--rs-muted); font-size:.82rem; }
hr { border-color:var(--rs-border); }
@media(max-width:850px) {
 .block-container { padding-top:4.5rem; padding-left:1rem; padding-right:1rem; }
 .rs-hero { padding:10px 0 14px; flex-direction:column; align-items:flex-start; gap:12px; }
 .rs-mandate { padding:0 0 0 12px; width:100%; min-width:0; }
 .rs-brand-product { display:none; }
 .rs-workers { grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px 10px; padding:12px 8px; }
 .rs-pipeline { gap:8px 16px; }
 [data-testid="stMetricValue"] { font-size:1.2rem; }
 .stApp:not(:has(.st-key-mcp_landing)) h1 { font-size:1.65rem !important; }
}
</style>
"""


def apply_presentation():
    st.markdown(CSS, unsafe_allow_html=True)


def navigation():
    st.markdown(
        '<div class="rs-brand"><span class="rs-monogram">RS</span>'
        '<span class="rs-brand-name">RetailScope AI</span>'
        '<span class="rs-brand-product">Acquisition workspace</span></div>',
        unsafe_allow_html=True,
    )
    return st.radio("Workspace", ["Find deals", "Center analysis"], horizontal=True,
                    key="retailscope_route", label_visibility="collapsed")


def render_analysis_mode(source):
    st.caption("Data source: " + str(source))


def render_analysis_header():
    st.markdown('<div class="rs-eyebrow">CENTER ANALYSIS</div>', unsafe_allow_html=True)
    st.title("Center analysis")
    st.caption("Keep rent-roll evidence, assumptions and investment-return gaps in view.")
