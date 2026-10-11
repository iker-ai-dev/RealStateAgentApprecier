"""Shared presentation only. No research, model calls or credential access."""
import streamlit as st

CSS = """
<style>
:root { --rs-navy:#222a2a; --rs-teal:#087f82; --rs-ink:#303939; --rs-muted:#606d6d; }
.stApp { background:#fff; color:var(--rs-ink); }
.block-container { max-width:1320px; padding-top:4rem; padding-bottom:2rem; }
h1,h2,h3 { color:var(--rs-navy); letter-spacing:0; }
h1 { font-size:1.9rem !important; line-height:1.2 !important; }
h2 { font-size:1.65rem !important; } h3 { font-size:1.2rem !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:#53647a !important; line-height:1.5; opacity:1; }
[data-testid="stVerticalBlockBorderWrapper"]>div { border-color:#d9e2e0 !important; border-radius:6px !important; background:#fff; }
[data-testid="stMetric"] { background:#fff; border:0; border-radius:0; padding:8px 0; min-width:0; }
[data-testid="stMetricLabel"] p { color:#617184; font-size:.77rem; }
[data-testid="stMetricValue"] { color:#222a2a; font-size:1.4rem; overflow:visible; white-space:normal; }
[data-testid="stMetricDelta"] { font-size:.72rem; white-space:normal; }
[data-testid="stButton"] button { border-radius:6px; min-height:40px; font-weight:600; }
[data-testid="stTextInput"] input { border-radius:8px; }
[data-testid="stExpander"] { background:#fff; border-radius:6px; }
[data-testid="stChatMessage"] { background:#fff; border:1px solid #d9e2e0; border-radius:6px; }
.rs-brand { display:flex; align-items:center; gap:10px; color:#222a2a; font-weight:750; font-size:1rem; margin-bottom:.3rem; }
.rs-monogram { background:#087f82; color:#fff; border-radius:6px; padding:6px 9px; font-size:.8rem; letter-spacing:0; }
.rs-hero { background:#fff; border-bottom:1px solid #d9e2e0; border-radius:0; padding:12px 0 18px; color:#222a2a; margin:0 0 12px; display:flex; gap:24px; align-items:center; justify-content:space-between; }
.rs-eyebrow { color:#087f82; text-transform:uppercase; letter-spacing:0; font-size:.7rem; font-weight:700; margin-bottom:6px; }
.rs-hero h1 { color:#222a2a; font-size:1.9rem; line-height:1.2; margin:0 0 8px; padding:0; max-width:690px; }
.rs-hero p { color:#606d6d; line-height:1.5; font-size:.9rem; margin:0; max-width:650px; }
.rs-mandate { min-width:190px; border-left:3px solid #df705f; padding-left:16px; }
.rs-mandate strong { display:block; color:#222a2a; font-size:1.1rem; margin-bottom:4px; }
.rs-mandate span { display:block; color:#606d6d; font-size:.78rem; line-height:1.6; }
.rs-mode { border:0; border-left:3px solid #df705f; border-radius:0; background:#f8f4f3; padding:9px 12px; color:#404b49; font-size:.8rem; margin-bottom:10px; }
.rs-tag { display:inline-block; border-radius:6px; font-size:.69rem; font-weight:650; padding:4px 8px; margin:0 5px 8px 0; background:#edf2f7; color:#4e6175; }
.rs-tag-teal { background:#e6f4f2; color:#076b70; } .rs-tag-amber { background:#fff3db; color:#826021; }
.rs-card-title { font-size:1.1rem; font-weight:730; color:#222a2a; margin:1px 0 5px; line-height:1.3; overflow-wrap:anywhere; }
.rs-place { font-size:.8rem; color:#606d6d; margin-bottom:10px; overflow-wrap:anywhere; }
.rs-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; border-top:1px solid #d9e2e0; border-bottom:1px solid #d9e2e0; padding:10px 0; margin-bottom:8px; }
.rs-facts span { display:block; color:#6b788a; font-size:.7rem; margin-bottom:5px; }
.rs-facts strong { display:block; color:#222a2a; font-size:1rem; letter-spacing:0; line-height:1.3; overflow-wrap:anywhere; }
.rs-mini-heading { color:#404b49; font-size:.7rem; font-weight:750; text-transform:uppercase; letter-spacing:0; margin:8px 0 5px; }
.rs-note { color:#617184; font-size:.83rem; line-height:1.5; margin:0; }
.rs-empty { text-align:center; background:#fff; border:1px dashed #c8d6d2; border-radius:6px; padding:24px; margin:10px 0 16px; }
.rs-empty h3 { margin:0 0 10px; }.rs-empty p { margin:0 auto; max-width:650px; color:#617184; line-height:1.6; }
.rs-pipeline { display:flex; gap:20px; font-size:.8rem; color:#606d6d; margin:6px 0 10px; flex-wrap:wrap; }
.rs-pipeline b { color:#087f82; }.rs-pipeline span { padding:4px 0; }
.rs-workers { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; padding:12px 0; border-top:1px solid #d9e2e0; border-bottom:1px solid #d9e2e0; }
.rs-workers strong { display:block; font-size:.8rem; color:#303939; overflow-wrap:anywhere; }
.rs-workers span { display:block; font-size:.78rem; color:#087f82; margin-top:5px; }
[data-testid="stLinkButton"] a { border-radius:6px; }
* { letter-spacing:0 !important; }
.section-label { color:#435970; font-size:.75rem; font-weight:700; margin:1rem 0 .35rem; text-transform:uppercase; }
.note { color:#617184; font-size:.85rem; }
@media(max-width:850px) { .rs-hero { padding:12px 0; flex-direction:column; align-items:flex-start; gap:12px; }.rs-mandate { padding:0 0 0 12px; width:100%; min-width:0; }.block-container { padding-top:4rem; padding-left:1rem; padding-right:1rem; } [data-testid="stMetricValue"] { font-size:1.15rem; } .rs-workers { grid-template-columns:repeat(2,minmax(0,1fr)); }.rs-pipeline { gap:8px 16px; } }
</style>
"""


def apply_presentation():
    st.markdown(CSS, unsafe_allow_html=True)


def navigation():
    st.markdown('<div class="rs-brand"><span class="rs-monogram">RS</span> RetailScope AI</div>', unsafe_allow_html=True)
    return st.radio("Workspace", ["Find deals", "Center analysis"], horizontal=True, key="retailscope_route", label_visibility="collapsed")


def render_analysis_mode(source):
    st.caption("Data source: " + str(source))


def render_analysis_header():
    st.markdown('<div class="rs-eyebrow" style="color:#087f82">CENTER ANALYSIS</div>', unsafe_allow_html=True)
    st.title("Center analysis")
    st.caption("Keep rent-roll evidence, assumptions and investment-return gaps in view.")
