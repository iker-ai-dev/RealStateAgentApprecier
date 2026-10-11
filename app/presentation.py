"""Shared presentation only. No research, model calls or credential access."""
from html import escape
import streamlit as st

CSS = """
<style>
:root { --rs-navy:#142a43; --rs-teal:#087f82; --rs-ink:#24364b; --rs-muted:#617184; }
.stApp { background:#f3f6fa; color:var(--rs-ink); }
.block-container { max-width:1440px; padding-top:2.4rem; padding-bottom:3rem; }
h1,h2,h3 { color:var(--rs-navy); letter-spacing:-.035em; }
h2 { font-size:1.65rem !important; } h3 { font-size:1.2rem !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:#53647a !important; line-height:1.5; opacity:1; }
[data-testid="stVerticalBlockBorderWrapper"]>div { border-color:#dce4ee !important; border-radius:14px !important; background:#fff; }
[data-testid="stMetric"] { background:#fff; border:1px solid #dce4ee; border-radius:12px; padding:12px 14px; min-width:0; }
[data-testid="stMetricLabel"] p { color:#617184; font-size:.77rem; }
[data-testid="stMetricValue"] { color:#142a43; font-size:clamp(1.05rem,1.5vw,1.65rem); overflow:visible; white-space:normal; }
[data-testid="stMetricDelta"] { font-size:.72rem; white-space:normal; }
[data-testid="stButton"] button { border-radius:9px; min-height:42px; font-weight:600; }
[data-testid="stTextInput"] input { border-radius:8px; }
[data-testid="stExpander"] { background:#fff; border-radius:12px; }
[data-testid="stChatMessage"] { background:#fff; border:1px solid #e2e8f0; border-radius:12px; }
.rs-brand { display:flex; align-items:center; gap:10px; color:#142a43; font-weight:750; font-size:1rem; margin-bottom:1rem; }
.rs-monogram { background:#087f82; color:#fff; border-radius:8px; padding:6px 9px; font-size:.8rem; letter-spacing:.04em; }
.rs-hero { background:linear-gradient(115deg,#142a43,#1d4059); border-radius:18px; padding:24px 28px; color:#fff; margin:12px 0 16px; display:flex; gap:32px; align-items:center; justify-content:space-between; }
.rs-eyebrow { color:#9fdae0; text-transform:uppercase; letter-spacing:.14em; font-size:.7rem; font-weight:700; margin-bottom:10px; }
.rs-hero h1 { color:#fff; font-size:clamp(1.8rem,2.7vw,2.35rem); line-height:1.13; margin:0 0 12px; padding:0; max-width:690px; }
.rs-hero p { color:#d4e0eb; line-height:1.6; font-size:.94rem; margin:0; max-width:650px; }
.rs-mandate { min-width:190px; border-left:1px solid #5f778d; padding-left:28px; }
.rs-mandate strong { display:block; color:#fff; font-size:1.25rem; margin-bottom:4px; }
.rs-mandate span { display:block; color:#c3d3e2; font-size:.8rem; line-height:1.7; }
.rs-mode { border:1px solid #cfdfe9; border-left:3px solid #087f82; border-radius:8px; background:#eaf2f7; padding:10px 14px; color:#35536e; font-size:.82rem; margin-bottom:18px; }
.rs-tag { display:inline-block; border-radius:6px; font-size:.69rem; font-weight:650; padding:4px 8px; margin:0 5px 8px 0; background:#edf2f7; color:#4e6175; }
.rs-tag-teal { background:#e6f4f2; color:#076b70; } .rs-tag-amber { background:#fff3db; color:#826021; }
.rs-card-title { font-size:1.17rem; font-weight:730; color:#142a43; margin:1px 0 5px; line-height:1.3; overflow-wrap:anywhere; }
.rs-place { font-size:.83rem; color:#617184; margin-bottom:16px; }
.rs-facts { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; border-top:1px solid #e3eaf2; border-bottom:1px solid #e3eaf2; padding:15px 0; margin-bottom:14px; }
.rs-facts span { display:block; color:#6b788a; font-size:.7rem; margin-bottom:5px; }
.rs-facts strong { display:block; color:#142a43; font-size:1.1rem; letter-spacing:-.025em; line-height:1.3; overflow-wrap:anywhere; }
.rs-mini-heading { color:#3b536c; font-size:.7rem; font-weight:750; text-transform:uppercase; letter-spacing:.07em; margin:12px 0 6px; }
.rs-note { color:#617184; font-size:.83rem; line-height:1.5; margin:0; }
.rs-empty { text-align:center; background:#fff; border:1px dashed #c8d6e5; border-radius:14px; padding:40px 24px; margin:10px 0 20px; }
.rs-empty h3 { margin:0 0 10px; }.rs-empty p { margin:0 auto; max-width:650px; color:#617184; line-height:1.6; }
.rs-pipeline { display:flex; gap:14px; font-size:.8rem; color:#617184; margin:8px 0 20px; flex-wrap:wrap; }
.rs-pipeline b { color:#087f82; }.rs-pipeline span { padding:7px 11px; border:1px solid #dce4ee; background:#fff; border-radius:7px; }
.section-label { color:#435970; font-size:.75rem; font-weight:700; margin:1rem 0 .35rem; text-transform:uppercase; }
.note { color:#617184; font-size:.85rem; }
@media(max-width:850px) { .rs-hero { padding:24px; flex-direction:column; align-items:flex-start; gap:20px; }.rs-mandate { border-left:0; border-top:1px solid #5f778d; padding:16px 0 0; width:100%; }.block-container { padding-top:4rem; } [data-testid="stMetricValue"] { font-size:1.15rem; } }
</style>
"""


def apply_presentation():
    st.markdown(CSS, unsafe_allow_html=True)


def navigation():
    st.markdown('<div class="rs-brand"><span class="rs-monogram">RS</span> RetailScope AI</div>', unsafe_allow_html=True)
    return st.radio("Workspace", ["Find deals", "Center analysis"], horizontal=True, key="retailscope_route", label_visibility="collapsed")


def render_analysis_mode(source):
    demo = "demo" in source.lower() or "sample" in source.lower()
    label = "DEMO MODEL" if demo else "UNVERIFIED DATA"
    st.markdown('<div class="rs-mode"><b>' + label + '</b> · ' + escape(source) + ' · Legacy estimates are illustrative. No investment decision or evaluated return target.</div>', unsafe_allow_html=True)


def render_analysis_header():
    st.markdown('<div class="rs-eyebrow" style="color:#087f82">CENTER ANALYSIS</div>', unsafe_allow_html=True)
    st.title("Inspect the center. Test the thesis.")
    st.caption("Keep rent-roll evidence, assumptions and investment-return gaps in view.")
