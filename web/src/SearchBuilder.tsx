import { FileText } from "lucide-react";
import { type AgentInput } from "./api";

export const cities = [
  "Atlanta, GA",
  "Austin, TX",
  "Boston, MA",
  "Charlotte, NC",
  "Chicago, IL",
  "Columbus, OH",
  "Dallas, TX",
  "Denver, CO",
  "Detroit, MI",
  "Houston, TX",
  "Indianapolis, IN",
  "Jacksonville, FL",
  "Las Vegas, NV",
  "Los Angeles, CA",
  "Miami, FL",
  "Minneapolis, MN",
  "Nashville, TN",
  "New York, NY",
  "Orlando, FL",
  "Philadelphia, PA",
  "Phoenix, AZ",
  "Portland, OR",
  "Raleigh, NC",
  "Sacramento, CA",
  "Salt Lake City, UT",
  "San Antonio, TX",
  "San Diego, CA",
  "San Francisco, CA",
  "Seattle, WA",
  "Tampa, FL",
  "Washington, DC",
];
export const strategies = ["Value-add", "Income", "Redevelopment"];
export type SearchCriteria = Omit<AgentInput, "name">;
export const defaultCriteria: SearchCriteria = {
  city: "Phoenix, AZ",
  min_price: 1000000,
  max_price: 10000000,
  strategy: "Value-add",
};
export function searchPrompt(value: SearchCriteria) {
  return `Research for me open-air shopping centers and strip centers for sale in ${value.city}, United States, with an asking price between $${Number.isFinite(value.min_price) ? value.min_price / 1000000 : "..."} million and $${Number.isFinite(value.max_price) ? value.max_price / 1000000 : "..."} million. Focus on ${value.strategy.toLowerCase()} opportunities. Find original property listings and report asking price, area, occupancy, cap rate, annual NOI, tenant rents, population and median household income when sources provide them. Include source links and distinguish source-reported facts from unknowns. Do not assume a property fits the budget.`;
}

export function SearchBuilder({
  value,
  onChange,
  disabled = false,
}: {
  value: SearchCriteria;
  onChange: (value: SearchCriteria) => void;
  disabled?: boolean;
}) {
  function update(next: SearchCriteria) {
    onChange(next);
  }
  return (
    <fieldset className="search-builder" disabled={disabled}>
      <legend className="sr-only">Build search</legend>
      <details className="prompt-bank">
        <summary>Prompt bank</summary>
        <div className="preset-choices">
          {[
            { label: "Lease-up", strategy: "Value-add" },
            { label: "Stable income", strategy: "Income" },
            { label: "Reposition", strategy: "Redevelopment" },
          ].map((preset) => (
            <button
              type="button"
              className="text-button"
              key={preset.label}
              onClick={() => update({ ...value, strategy: preset.strategy })}
            >
              <FileText size={13} />
              {preset.label}
            </button>
          ))}
        </div>
      </details>
      <label>
        US city
        <select
          value={value.city}
          onChange={(event) => update({ ...value, city: event.target.value })}
        >
          {cities.map((city) => (
            <option key={city}>{city}</option>
          ))}
        </select>
      </label>
      <div className="budget-fields">
        <label>
          Min budget ($M)
          <input
            type="number"
            min="0"
            max="1000"
            step="0.1"
            required
            value={
              Number.isFinite(value.min_price) ? value.min_price / 1000000 : ""
            }
            onChange={(event) =>
              update({
                ...value,
                min_price: Math.round(event.target.valueAsNumber * 1000000),
              })
            }
          />
        </label>
        <label>
          Max budget ($M)
          <input
            type="number"
            min={
              Number.isFinite(value.min_price)
                ? (value.min_price + 100000) / 1000000
                : 0.1
            }
            max="1000"
            step="0.1"
            required
            value={
              Number.isFinite(value.max_price) ? value.max_price / 1000000 : ""
            }
            onChange={(event) =>
              update({
                ...value,
                max_price: Math.round(event.target.valueAsNumber * 1000000),
              })
            }
          />
        </label>
      </div>
      <label>
        Strategy
        <select
          value={value.strategy}
          onChange={(event) =>
            update({ ...value, strategy: event.target.value })
          }
        >
          {strategies.map((strategy) => (
            <option key={strategy}>{strategy}</option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
