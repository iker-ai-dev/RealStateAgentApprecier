"""Public listing discovery presentation. No paid calls or credential lookup.

Broker/listing assertions remain unverified; selection never populates legacy math.
"""
import json
import math
import hashlib
import os
from html import escape
from pathlib import Path

import streamlit as st
from app.listing_discovery import safe_public_url
from app.discovery_jobs import start_scan

LEAD_PATH = Path(__file__).parent / "data" / "listing_leads.json"
MAX_LEADS = 100
VACANCY_BANDS = {"10–60% vacancy": (10, 60), "10–35% vacancy": (10, 35), "35–60% vacancy": (35, 60)}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def safe_url(value):
    return safe_public_url(value)


def is_property_lead(lead):
    return lead.get("lead_kind") not in ("listing_directory", "market_report")


def source_notes(value):
    """Plain source data; do not execute or interpret HTML/instructions."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item)[:1200] for item in value[:12]]
    return [str(value)[:2400]]


def load_leads():
    """Bounded local normalized records; missing file is an honest empty state."""
    if not LEAD_PATH.exists():
        return [], None
    try:
        if LEAD_PATH.stat().st_size > 1_000_000:
            raise ValueError()
        payload = json.loads(LEAD_PATH.read_text(encoding="utf8"))
        if isinstance(payload, dict):
            payload = payload.get("leads")
        if not isinstance(payload, list) or len(payload) > MAX_LEADS or any(not isinstance(row, dict) for row in payload):
            raise ValueError()
        return payload, None
    except (OSError, ValueError, TypeError):
        return [], "Saved listing leads could not be read. Reload a valid public-source lead file."


def location(lead):
    return ", ".join(str(lead[x]) for x in ("city", "state") if lead.get(x)) or "Market unknown"


def lead_identity(lead):
    if lead.get("id") is not None:
        return str(lead["id"])
    return hashlib.sha256(json.dumps([lead.get("url"),lead.get("title"),lead.get("city"),lead.get("state")],default=str).encode()).hexdigest()[:20]


def vacancy(lead):
    occupied = number(lead.get("occupancy_pct"))
    return 100 - occupied if occupied is not None and 0 <= occupied <= 100 else None


def income_fact(lead):
    """A value alone does not establish compatible demographic coverage."""
    value = number(lead.get("median_household_income"))
    if value is None or value < 0 or not all(lead.get(k) for k in ("income_geography", "income_period")) or not safe_url(lead.get("income_source_url")):
        return None
    return value


def filter_leads(leads, *, market="All markets", sources=None, price=(1, 10), vacancy_band=(10, 60), include_unknown=True, minimum_income=None):
    """Disclosed listing filters only; no ranking, NOI/return inference or imputation."""
    kept = []
    for row in leads:
        if not isinstance(row, dict) or not is_property_lead(row):
            continue
        if market != "All markets" and location(row) != market:
            continue
        if sources and (row.get("source_name") or "Other / broker") not in sources:
            continue
        asking = number(row.get("asking_price"))
        if asking is None:
            if not include_unknown:
                continue
        elif not price[0] * 1_000_000 <= asking <= price[1] * 1_000_000:
            continue
        vac = vacancy(row)
        if vac is None:
            if not include_unknown:
                continue
        elif not vacancy_band[0] <= vac <= vacancy_band[1]:
            continue
        if minimum_income is not None:
            income = income_fact(row)
            if income is None or income < minimum_income:
                continue
        kept.append(row)
    return kept


def money(value):
    value = number(value)
    if value is None:
        return "Unknown"
    return "$" + (f"{value / 1_000_000:,.2f}m" if abs(value) >= 1_000_000 else f"{value:,.0f}")


def _text(value, fallback="Unknown"):
    return escape(str(value)) if value is not None and str(value).strip() else fallback


def why_investigate(lead):
    reasons = source_notes(lead.get("why_investigate"))
    if reasons:
        return ["Source-supplied lead rationale (unverified): " + " ".join(reasons)]
    asking = number(lead.get("asking_price"))
    vac = vacancy(lead)
    if asking is not None and 1_000_000 <= asking <= 10_000_000:
        reasons.append("Advertised price is inside the $1–10m search range.")
    if vac is not None and 10 <= vac <= 60:
        reasons.append("Reported vacancy leaves space to investigate for lease-up.")
    if income_fact(lead) is not None:
        reasons.append("A dated income observation is available; confirm its geography and basis.")
    return reasons or ["Public lead to investigate. Price, occupancy or demographic fit still needs evidence."]


def _card(lead, key):
    vac = vacancy(lead)
    occ = 100-vac if vac is not None else None
    gla = number(lead.get("gla_sf"))
    building = number(lead.get("building_sf"))
    area = gla if gla is not None and gla > 0 else building
    area_heading = "Rentable area" if gla is not None and gla > 0 else "Building area"
    demo = lead.get("is_demo") is True
    discovery = lead.get("source_status") == "discovery_only"
    tag = "Synthetic example" if demo else ("Search lead · page unverified" if discovery else "Listing assertion")
    occupied_label = f"{occ:g}%" if occ is not None else "Unknown"
    area_label = f"{area:,.0f} SF" if area is not None and area > 0 else "Unknown"
    st.markdown(
        '<span class="rs-tag rs-tag-teal">PUBLIC LEAD</span><span class="rs-tag">' + tag + '</span>'
        '<div class="rs-card-title">' + _text(lead.get("title"), "Untitled public lead") + '</div>'
        '<div class="rs-place">' + _text(location(lead)) + ' · ' + _text(lead.get("property_type"), "Type unknown") + '</div>'
        '<div class="rs-facts"><div><span>Asking price</span><strong>' + money(lead.get("asking_price")).replace("$","&#36;") + '</strong></div>'
        '<div><span>Reported occupancy</span><strong>' + occupied_label + '</strong></div>'
        '<div><span>' + area_heading + '</span><strong>' + area_label + '</strong></div></div>'
        '<div class="rs-mini-heading">Why investigate</div><p class="rs-note">' + escape(" ".join(why_investigate(lead))).replace("$","&#36;") + '</p>',
        unsafe_allow_html=True,
    )
    st.caption("Evidence status: " + ("Synthetic / unverified" if demo else ("Search result only / page unverified" if discovery else "Broker assertion / needs verification")) + ". Vacancy cause and tenant stability are unknown.")
    if lead.get("source_advertised_status"):
        st.caption("Availability recheck: " + str(lead["source_advertised_status"]))
    with st.expander("Source evidence & unknowns"):
        if lead.get("occupancy_basis"):
            st.text("Occupancy basis: " + str(lead["occupancy_basis"]).replace("_", " "))
        for field in ("risk_note", "qualification_note", "financial_note", "source_support_location"):
            if lead.get(field):
                st.text(str(lead[field]))
        for item in source_notes(lead.get("missing_fields")):
            st.text("Missing: " + item.replace("_", " "))
        for item in source_notes(lead.get("source_conflicts")):
            st.text("Source conflict: " + item)
    if vac is not None and vac > 35:
        st.caption("Elevated vacancy: test demand, access, achievable rent and capital needs before assuming lease-up.")
    url = safe_url(lead.get("url"))
    st.caption(f"{lead.get('source_name') or 'Source unknown'} · Listed {lead.get('listing_date') or 'date unknown'} · Retrieved {lead.get('retrieved_at') or 'date unknown'}")
    left, right = st.columns([.56,.44])
    with left:
        picked = st.button("Review this lead", key=key, type="primary", width="stretch", icon=":material/fact_check:")
    with right:
        if url:
            st.link_button("Open source ↗", url, width="stretch")
        else:
            st.button("Source unavailable", key=key + "_url", disabled=True, width="stretch")
    return picked


def render_lead_detail(lead):
    """Selected listing is kept apart from unrelated legacy demo underwriting."""
    with st.container(border=True):
        st.markdown('<div class="rs-eyebrow" style="color:#087f82">LEAD WORKSPACE · SOURCE REVIEW</div>', unsafe_allow_html=True)
        st.subheader(lead.get("title") or "Selected public lead")
        st.caption(location(lead) + " · Listing assertions are unverified. Active status: " + str(lead.get("active_status") or "unknown") + ".")
        st.info("Needs diligence. This listing has not been loaded into the center's rent-roll model. IRR and equity-return fit have not been calculated.")
        a,b,c = st.columns(3)
        a.metric("Asking price",money(lead.get("asking_price")))
        b.metric("Advertised NOI",money(lead.get("broker_noi")),help="Broker figure; T12/in-place/pro forma basis may be unknown.")
        cap = number(lead.get("cap_rate"))
        cap_label = (f"{cap:g}%" if lead.get("cap_rate_unit") == "percent" else f"{cap:g} · unit unknown") if cap is not None else "Unknown"
        c.metric("Advertised cap rate",cap_label,help="Listing assertion; explicit units are required before a cap-rate comparison.")
        left,right = st.columns(2,gap="large")
        with left:
            st.markdown("**Possible vacancy drivers**")
            st.write("Unknown. Investigate leasing execution and marketing, achievable asking rent, tenant mix, local retail demand, visibility/access and deferred capital work. Vacancy alone does not establish owner or manager failure.")
            supplied = source_notes(lead.get("vacancy_drivers"))
            if supplied:
                st.caption("Research hypotheses · unverified, not established causes")
                drivers = lead.get("vacancy_drivers")
                for item in drivers[:12] if isinstance(drivers, list) else [drivers]:
                    if isinstance(item, dict):
                        for label, field in (("Hypothesis", "hypothesis"), ("Source evidence", "evidence"),
                                             ("Counterevidence", "counterevidence")):
                            if item.get(field):
                                st.text(label + ": " + str(item[field])[:2400])
                        for question in source_notes(item.get("verification_questions")):
                            st.text("Verify: " + question)
                    else:
                        st.text(str(item)[:2400])
            st.markdown("**Why investigate · source rationale**")
            for item in source_notes(lead.get("why_investigate")) or ["No source rationale supplied. Test the listing screen before advancing."]:
                st.text(item)
            st.markdown("**Evidence to request**")
            st.write("Current rent roll and executed leases; vacant-suite history; leasing inquiries and lost-deal reasons; T12 collections/expenses; TI/LC and capex; pipeline and competing rents; active listing confirmation.")
        with right:
            st.markdown("**What would disprove the thesis**")
            st.write("Persistent weak demand, poor access, rents above achievable effective levels, recurring tenant failures, restrictive lease/co-tenancy terms or capital needs that overwhelm the priced return scenario.")
            for item in source_notes(lead.get("counterevidence")) + source_notes(lead.get("questions_to_verify")):
                st.text("Source-supplied counterevidence / question (unverified): " + item)
            income=income_fact(lead)
            st.markdown("**Income context · separate from NOI**")
            if income is None:
                st.write("Unknown. A matching geography, period and public income source are still needed.")
            else:
                st.write(f"${income:,.0f} median household income · {lead['income_geography']} · {lead['income_period']}. Basis: {lead.get('income_basis') or 'unknown'}. Source claim remains unverified.")
                st.link_button("Open income source ↗",safe_url(lead.get("income_source_url")))
        with st.expander("Listing record and source gaps"):
            st.write("Occupancy basis, tenant stability, source vintage and future income are unverified unless separately documented. A listing cap rate or NOI is not completed underwriting.")
            st.json({k:lead.get(k) for k in ("id","url","source_name","source_status","retrieved_at","listing_date","active_status","asking_price","occupancy_pct","occupancy_basis","gla_sf","building_sf","broker_noi","cap_rate","cap_rate_unit","property_type","lead_kind","source_support_location","missing_fields","source_conflicts","financial_note","income_tenant_area_share_pct","post_sale_income_unleased_pct")})
        county = lead.get("county_context")
        if isinstance(county, dict):
            with st.expander("County context · separate from the property's trade area"):
                st.text(str(county.get("geography") or "Geography unknown"))
                st.text("Historical county context only. These figures do not establish site demand, local trade-area income or a 5–10-year forecast.")
                st.json({k:county.get(k) for k in ("median_household_income_usd","income_period",
                          "population_change_pct","population_period","population_density_per_sq_mile","density_period","limitation")})
                url = safe_url(county.get("url"))
                if url:
                    st.link_button("Open county source ↗", url)


def render_discovery_status():
    job = st.session_state.get("radar_discovery_job")
    if job is None:
        return
    was_active = job.active
    job.poll()
    if was_active and not job.active:
        st.rerun(scope="app")
    heading, action = st.columns([3, 1])
    heading.markdown("**Background discovery agents** · Cached-source scan")
    if action.button("Refresh scan", icon=":material/refresh:", disabled=job.active, width="stretch"):
        st.session_state.radar_discovery_job = start_scan(
            st.session_state.radar_discovery_records, filter_leads,
            st.session_state.radar_discovery_filters)
        st.rerun(scope="app")
    states = job.statuses()
    for name, future in job.workers.items():
        if states[name] == "Complete":
            states[name] += f" · {future.result()['observed']} leads"
    st.markdown('<div class="rs-workers">' + ''.join(
        '<div><strong>' + escape(name) + '</strong><span>' + escape(state) + '</span></div>'
        for name, state in states.items()) + '</div>', unsafe_allow_html=True)
    completed = [future.result() for future in job.workers.values()
                 if future.done() and not future.cancelled() and future.exception() is None]
    count = sum(group["observed"] for group in completed)
    vintages = sorted({v for group in completed for v in group["vintages"]})
    vintage = ', '.join(sorted({v[:10] for v in vintages})) or "Not observed yet"
    st.caption(f"Observed {count} property leads · Snapshot retrieval vintage: {vintage} · No live web or LLM calls.")
    if job.error:
        st.error(job.error)
    elif job.result is not None:
        st.caption(f"Scan complete · {len(job.result['leads'])} review leads under the scan filters · "
                   f"{job.result['resources']} research resources excluded · Availability is not verified.")
    else:
        st.caption("Scanning saved snapshots in background. Saved leads remain available below.")


def render_deal_radar(leads=None):
    """Return selected lead or None; render locally without any live research."""
    st.markdown('<div class="rs-hero"><div><div class="rs-eyebrow">ACQUISITION WORKSPACE</div><h1>Public deal radar</h1><p>Review partially vacant retail centers from saved public-source evidence.</p></div><div class="rs-mandate"><strong>$1–10m</strong><span>U.S. open-air centers · 10–60% vacancy</span><span>20%+ IRR / 6–7× equity goals · not evaluated</span></div></div>',unsafe_allow_html=True)
    st.markdown('<div class="rs-mode"><b>DISCOVERY ONLY</b> · Public listing assertions are unverified. Missing facts stay unknown. No investment decision or return-target fit.</div>',unsafe_allow_html=True)
    st.markdown('<div class="rs-pipeline"><span><b>01</b> Background agents</span><span><b>02</b> Review leads &amp; evidence</span><span><b>03</b> Financial analysis · needs documents</span></div>',unsafe_allow_html=True)
    # Root-owned explicit-search control. Importing/rendering does not spend credits.
    try:
        from app.research_ui import render_listing_search
    except ImportError:
        render_listing_search = None
    warning = None
    if leads is None:
        leads = st.session_state.get("listing_leads")
    if leads is None:
        leads, warning = load_leads()
    if not isinstance(leads,list) or len(leads)>MAX_LEADS:
        leads=[]; warning="Listing leads must be a bounded list of public-source records."
    leads=[x for x in leads if isinstance(x,dict)]
    if warning:st.warning(warning)
    discovery_panel = st.container()
    resources = [row for row in leads if not is_property_lead(row)]
    candidates = [row for row in leads if is_property_lead(row)]
    if resources:
        with st.expander(f"Research sources · {len(resources)} directories / market reports"):
            st.caption("Navigation and market research only. These are not property candidates and do not count toward the shortlist.")
            for record in resources:
                st.text(str(record.get("title") or "Public research source"))
                url = safe_url(record.get("url"))
                if url:
                    st.link_button("Open research source ↗", url)
    with st.expander("Screening filters"):
        a,b,c = st.columns([1.15,1,1])
        with a:
            markets=["All markets"]+sorted({location(x) for x in leads if location(x)!="Market unknown"})
            market=st.selectbox("Market",markets,key="radar_market")
        with b:
            price=st.slider("Asking price · $m",1.0,10.0,(1.0,10.0),.25,key="radar_price")
        with c:
            band=st.selectbox("Reported vacancy",list(VACANCY_BANDS),key="radar_vacancy")
        a,b,c = st.columns([1.15,1,1])
        with a:
            source_names=sorted({str(x.get("source_name") or "Other / broker") for x in leads})
            sources=st.multiselect("Listing sources",source_names,key="radar_sources",placeholder="All public sources")
        with b:
            income_choice=st.selectbox("Median household income",["Any coverage","Published $80k+","Published $100k+","Published $120k+"],key="radar_income")
        with c:
            include_unknown=st.checkbox("Keep price / occupancy unknowns",True,key="radar_unknowns")
            st.caption("Unknowns are leads to verify, never filter matches.")
    minimum={"Any coverage":None,"Published $80k+":80000,"Published $100k+":100000,"Published $120k+":120000}[income_choice]
    scan_filters = dict(market=market, sources=sources, price=price, vacancy_band=VACANCY_BANDS[band],
                        include_unknown=include_unknown, minimum_income=minimum)
    signature = hashlib.sha256(json.dumps([leads, scan_filters], sort_keys=True, default=str).encode()).hexdigest()
    job = st.session_state.get("radar_discovery_job")
    if job is not None:
        job.poll()
    if job is None or st.session_state.get("radar_discovery_signature") != signature:
        if job is not None and job.active:
            job.cancel()
        st.session_state.radar_discovery_job = start_scan(leads, filter_leads, scan_filters)
        st.session_state.radar_discovery_signature = signature
    st.session_state.radar_discovery_records = leads
    st.session_state.radar_discovery_filters = scan_filters
    with discovery_panel:
        interval = "1s" if st.session_state.radar_discovery_job.active else None
        st.fragment(run_every=interval)(render_discovery_status)()
    if render_listing_search is not None and os.getenv("RETAILSCOPE_PUBLIC_DEMO", "").lower() != "true":
        with st.expander("Optional live research connection"):
            render_listing_search()
    shown=filter_leads(leads,market=market,sources=sources,price=price,vacancy_band=VACANCY_BANDS[band],include_unknown=include_unknown,minimum_income=minimum)
    header,aside=st.columns([.7,.3])
    header.subheader("Public listing leads")
    aside.caption(f"{len(shown)} shown / {len(candidates)} property leads · source order")
    st.caption("Crexi, LoopNet and broker pages are discovery sources. Confirm listing availability and advertised facts directly. Income filters use sourced geography/period coverage; they do not establish site demand.")
    if not shown:
        st.markdown('<div class="rs-empty"><h3>'+('No leads match these filters' if leads else 'Start with public listing evidence')+'</h3><p>'+('Broaden the filters or keep unknown price and occupancy records for follow-up.' if leads else 'No public leads are loaded yet. Add source-backed Crexi, LoopNet or broker records, or use an explicitly configured research connection. No searches run automatically.')+'</p></div>',unsafe_allow_html=True)
    selected_id=st.session_state.get("radar_selected_id")
    # Stable lead identity keeps a queued selection tied to its source record.
    for start in range(0,len(shown),2):
        columns=st.columns(2,gap="large")
        for index,lead in enumerate(shown[start:start+2],start):
            with columns[index-start],st.container(border=True):
                if _card(lead,"radar_review_"+lead_identity(lead)+"_"+str(sum(lead_identity(x)==lead_identity(lead) for x in shown[:index]))):
                    selected_id=lead_identity(lead)
                    st.session_state.radar_selected_id=selected_id
    selected=next((lead for index,lead in enumerate(candidates) if lead_identity(lead)==selected_id),None)
    if selected is not None:
        if selected not in shown:
            st.caption("The selected lead is outside the current filters; its diligence workspace remains open.")
        st.divider()
        render_lead_detail(selected)
    st.caption("Return goals: 20%+ annualized IRR including sale and 6–7× total cash returned / all equity invested. Compare 5, 7 and 10 years; later equity contributions count. A dated return engine and verified deal documents are required.")
    return selected
