import { ExternalLink, Globe2, MapPin } from "lucide-react";
import { useState } from "react";
import { factKeys, preferredProperty, safeUrl, type Lead } from "./api";

const labels: Record<(typeof factKeys)[number], string> = {
  asking_price: "Asking price",
  area_sqft: "Area",
  cap_rate: "Cap rate",
  occupancy_pct: "Occupancy",
  annual_noi: "Annual NOI",
  asking_rent_psf: "Asking rent / sq ft",
  population: "Population",
  median_household_income: "Median household income",
};
export function LeadDetails({ lead, selectedPropertyId, onPropertySelect }: {
  lead: Lead;
  selectedPropertyId?: string;
  onPropertySelect?: (id: string) => void;
}) {
  const [localPropertyId, setLocalPropertyId] = useState<string>();
  const properties = lead.properties ?? [];
  const property = properties.find((item) => item.id === (selectedPropertyId ?? localPropertyId)) ?? preferredProperty(lead);
  return <>
    {property && <div className="source-properties">
      <p className="muted">{lead.title}</p>
      <label>
        Properties in this source ({properties.length})
        <select aria-label="Property from source" value={property.id} onChange={(event) => {
          setLocalPropertyId(event.target.value);
          onPropertySelect?.(event.target.value);
        }}>
          {properties.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label>
    </div>}
    <PropertyDetails lead={property ? { ...lead, ...property, properties: undefined } : lead} />
  </>;
}

function PropertyDetails({ lead }: { lead: Lead }) {
  const provided = (value?: string | null) =>
    value && !["unknown", "n/a", "none"].includes(value.toLowerCase())
      ? value
      : undefined;
  const knownCount = factKeys.filter(
    (key) =>
      lead.facts?.[key]?.status === "source_reported" &&
      lead.facts[key].value !== null,
  ).length;
  return (
    <div className="lead-details">
      <span className="property-status">
        {provided(lead.property_type) ||
          provided(lead.status) ||
          "Research source"}
      </span>
      <h2>{lead.title}</h2>
      {provided(lead.location) && (
        <p className="property-location">
          <MapPin size={14} />
          {lead.location}
        </p>
      )}
      <h3>Source-reported facts</h3>
      {!knownCount && (
        <p className="muted">
          No property-level figures could be extracted from the saved source.
        </p>
      )}
      {knownCount > 0 && <dl className="fact-grid">
        {factKeys.map((key) => {
          const fact = lead.facts?.[key];
          const known =
            fact?.status === "source_reported" &&
            fact.value !== null &&
            Number.isFinite(fact.value);
          const url = safeUrl(fact?.source_url ?? undefined);
          return (
            <div key={key} className="fact-metric">
              <dt>{labels[key]}</dt>
              <dd>
                {known ? (
                  <>
                    {fact.value!.toLocaleString("en-US", {
                      maximumFractionDigits: 2,
                    })}{" "}
                    <small>{fact.unit}</small>
                  </>
                ) : (
                  "Not provided"
                )}
              </dd>
              {known &&
                key === "occupancy_pct" &&
                fact.value! >= 0 &&
                fact.value! <= 100 && (
                  <meter
                    min={0}
                    max={100}
                    value={fact.value!}
                    aria-label="Source-reported occupancy"
                  />
                )}
              {known && (fact.quote || url) && (
                <details>
                  <summary>Evidence</summary>
                  {fact.quote && <blockquote>{fact.quote}</blockquote>}
                  {url && (
                    <a href={url} target="_blank" rel="noopener noreferrer">
                      Fact source <ExternalLink size={11} />
                    </a>
                  )}
                </details>
              )}
            </div>
          );
        })}
      </dl>}
      {knownCount > 0 && <p className="muted">Source claims, not verified valuations.</p>}
      <h3>Source summary</h3>
      <p className="property-summary">
        {lead.summary || "No summary supplied by the source."}
      </p>
      <dl className="property-meta">
        <div>
          <dt>Retrieved</dt>
          <dd>
            {lead.retrieved_at
              ? new Date(lead.retrieved_at).toLocaleString()
              : "Not supplied"}
          </dd>
        </div>
      </dl>
      {safeUrl(lead.url) ? (
        <a
          className="source-button"
          href={safeUrl(lead.url)}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Globe2 size={15} />
          Open original source
          <ExternalLink size={14} />
        </a>
      ) : (
        <p className="muted">Source link unavailable</p>
      )}
    </div>
  );
}
