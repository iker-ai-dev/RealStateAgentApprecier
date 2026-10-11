"""Host-rendered research facts; legacy valuation is illustrative, never a buy gate."""
import hashlib
import json
import math

import pandas as pd

TARGET_IRR = 0.20
TARGET_MULTIPLE = (6.0, 7.0)
HOLD_YEARS = (5, 7, 10)
GATE = "Needs diligence — investor return targets not evaluated"
DISCLAIMER = (
    "Research/demo only. Data and legacy model assumptions are unverified. "
    "No purchase decision or completed appraisal."
)


def context_fingerprint(property_row, tenants, market, comps, source="unknown"):
    payload = [property_row.to_dict(), tenants.to_dict("records"), market.to_dict(),
               comps.to_dict("records"), source]
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def validate_model_inputs(property_row, tenants, market, comps):
    """Reject invalid arithmetic; unknown document/provenance data stays a diligence gap."""
    if tenants.empty or comps.empty:
        raise ValueError("Rent-roll rows and comparison rows are required for the illustrative model.")
    checks = [(property_row, "gla_sf", True), (property_row, "asking_price", True),
              (property_row, "occupancy_pct", False), (market, "median_income", False),
              (market, "growth_5yr_pct", False)]
    for row, field, positive in checks:
        value = float(row[field])
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError("Model inputs must be finite; area and asking price must be positive.")
    if not 0 <= float(property_row["occupancy_pct"]) <= 100:
        raise ValueError("Occupancy must be between zero and 100 percent.")
    for frame, fields in ((tenants, ("area_sf", "rent_psf")),
                          (comps, ("price_psf", "cap_rate"))):
        for field in fields:
            values = pd.to_numeric(frame[field], errors="raise")
            if not all(math.isfinite(float(v)) and v >= 0 for v in values):
                raise ValueError("Rent-roll and comparison inputs must be finite and nonnegative.")
    if (comps["cap_rate"] <= 0).any() or (comps["cap_rate"] >= 1).any():
        raise ValueError("Comparable cap rates must be positive decimal fractions below one.")


def diligence_flags(property_row, tenants):
    flags = ["Source dates, verified executed leases, T12 collections/expenses and lease comparables are missing.",
             "A dated equity cash-flow model with financing, TI/LC, capex, carry and sale costs is required.",
             "20%+ annualized IRR including sale and a separate 6–7× total equity-return goal are not calculated."]
    total = float(tenants["area_sf"].sum())
    occupied = float(tenants.loc[tenants["category"].str.lower() != "vacant", "area_sf"].sum())
    gla = float(property_row["gla_sf"])
    if not math.isclose(total, gla, rel_tol=0.01):
        flags.append("Rent-roll area does not reconcile to property GLA; physical occupancy is unverified.")
    if not math.isclose(occupied / gla, float(property_row["occupancy_pct"]) / 100, abs_tol=0.01):
        flags.append("Reported occupancy does not reconcile to occupied rent-roll area.")
    if pd.to_datetime(tenants.loc[tenants["category"].str.lower() != "vacant", "lease_end"], errors="coerce").isna().any():
        flags.append("Unknown lease expirations prevent a complete rollover assessment.")
    if not 1_000_000 <= float(property_row["asking_price"]) <= 10_000_000:
        flags.append("Asking price is outside the confirmed $1–10 million acquisition range.")
    return flags


def render_research_answer(topic, property_row, tenants, market, comps, valuation, *, provider="deterministic fallback"):
    """LLM can choose a topic; all financial text is rendered from host data and policy."""
    lines = [DISCLAIMER, "Generation: " + provider + ".", GATE + "."]
    if topic == "rent":
        lines.append("Supplied in-place rent: ${:,.2f}/SF/year; rent basis and source date are unknown. "
                     "The legacy heuristic rent is not an observed market rent.".format(valuation.current_rent_psf))
    elif topic == "demographics":
        lines.append("Supplied trade area: {} miles; population {:,.0f}; household median income ${:,.0f}. "
                     "Dates, geographic method and income dollar year are unknown. Population density and "
                     "future household income are unknown. The growth field lacks a verified definition.".format(
                         market["trade_area_miles"], float(market["population"]), float(market["median_income"])))
    elif topic == "value":
        lines.append("Illustrative legacy blended value: ${:,.0f}; supplied asking price: ${:,.0f}. "
                     "Value-versus-price spread is not an equity multiple, cash-on-cash return or IRR.".format(
                         valuation.blended_value, valuation.asking_price))
    lines.extend(diligence_flags(property_row, tenants))
    lines.append("Compare 5-, 7- and 10-year scenarios after collecting the missing inputs. "
                 "Count all initial and later equity contributions; report annual operating cash-on-cash separately. "
                 "The 6–7× range is a goal, not a ceiling or a promised return.")
    return "\n\n".join(lines)
