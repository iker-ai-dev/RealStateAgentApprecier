import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Calculator,
  CircleAlert,
  LoaderCircle,
  MessageSquare,
} from "lucide-react";
import {
  api,
  errorMessage,
  type CalculatedScenario,
  type ScenarioInput,
  type ScenarioResult,
} from "./api";

const fields = [
  {
    key: "purchase_price",
    label: "Purchase price",
    unit: "$",
    required: true,
    min: 0.01,
    max: 1e10,
  },
  {
    key: "annual_noi",
    label: "Annual NOI",
    unit: "$",
    required: true,
    min: 0,
    max: 1e9,
  },
  {
    key: "exit_cap_rate",
    label: "Exit cap rate",
    unit: "%",
    required: true,
    min: 0.001,
    max: 50,
  },
  {
    key: "hold_years",
    label: "Hold period",
    unit: "yr",
    required: false,
    min: 1,
    max: 30,
  },
  {
    key: "loan_to_value",
    label: "Loan to value",
    unit: "%",
    required: false,
    min: 0,
    max: 99.999,
  },
  {
    key: "interest_rate",
    label: "Interest rate",
    unit: "%",
    required: false,
    min: 0,
    max: 50,
  },
  {
    key: "noi_growth",
    label: "NOI growth",
    unit: "%",
    required: false,
    min: -50,
    max: 50,
  },
] as const;
type FieldKey = (typeof fields)[number]["key"];
type Values = Record<FieldKey, string>;
const blank: Values = {
  purchase_price: "",
  annual_noi: "",
  exit_cap_rate: "",
  hold_years: "7",
  loan_to_value: "",
  interest_rate: "",
  noi_growth: "",
};
const currency = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);

export function scenarioInput(values: Values): ScenarioInput {
  const result: Record<string, number> = {};
  for (const field of fields) {
    if (values[field.key].trim() === "") {
      if (field.required) throw new Error(`${field.label} is required.`);
      continue;
    }
    const value = Number(values[field.key]);
    if (
      !Number.isFinite(value) ||
      value < field.min ||
      value > field.max ||
      (field.key === "hold_years" && !Number.isInteger(value))
    )
      throw new Error(`Enter a valid ${field.label.toLowerCase()}.`);
    result[field.key] = field.unit === "%" ? value / 100 : value;
  }
  return result as ScenarioInput;
}

function assumptionValue(key: string, value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (
      [
        "exit_cap_rate",
        "noi_growth",
        "loan_to_value",
        "interest_rate",
        "acquisition_cost_pct",
        "disposition_cost_pct",
      ].includes(key)
    )
      return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 3 })}%`;
    if (
      [
        "purchase_price",
        "annual_noi",
        "initial_capex",
        "annual_capex",
      ].includes(key)
    )
      return currency(value);
    return value.toLocaleString();
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return typeof value === "string" ? value : "Not supplied";
}

export function ScenarioForm({
  calculated,
  onCalculated,
  onInvalidate,
  onAsk,
}: {
  calculated?: CalculatedScenario;
  onCalculated: (scenario: CalculatedScenario) => void;
  onInvalidate: () => void;
  onAsk: () => void;
}) {
  const [values, setValues] = useState<Values>(() => {
    const values = { ...blank };
    if (calculated)
      for (const field of fields) {
        const value = calculated.input[field.key];
        if (value !== undefined)
          values[field.key] = String(field.unit === "%" ? value * 100 : value);
      }
    return values;
  });
  const [result, setResult] = useState<ScenarioResult | undefined>(
    calculated?.result,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (controller.current) return;
    let input: ScenarioInput;
    try {
      input = scenarioInput(values);
    } catch (error) {
      setError(errorMessage(error));
      return;
    }
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setError("");
    setResult(undefined);
    onInvalidate();
    try {
      const result = await api.scenario(input, request.signal);
      if (!request.signal.aborted) {
        setResult(result);
        onCalculated({ input, result });
      }
    } catch (error) {
      if (!request.signal.aborted) setError(errorMessage(error));
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <div className="scenario">
      <form onSubmit={submit}>
        <div className="scenario-heading">
          <Calculator size={17} />
          <h3>User assumptions</h3>
        </div>
        <div className="scenario-fields">
          {fields.map((field) => (
            <div className="scenario-field" key={field.key}>
              <label htmlFor={`scenario-${field.key}`}>
                {field.label}
                {field.required && <span aria-label="required"> *</span>}
              </label>
              <div className="number-input">
                <input
                  id={`scenario-${field.key}`}
                  type="number"
                  inputMode="decimal"
                  min={field.min}
                  max={field.max}
                  step={field.key === "hold_years" ? 1 : "any"}
                  required={field.required}
                  value={values[field.key]}
                  disabled={busy}
                  onChange={(event) => {
                    setValues((current) => ({
                      ...current,
                      [field.key]: event.target.value,
                    }));
                    setResult(undefined);
                    setError("");
                    onInvalidate();
                  }}
                />
                <span>{field.unit}</span>
              </div>
            </div>
          ))}
        </div>
        <p className="scenario-note">
          Required inputs are not inferred from listings. Optional blanks use
          API defaults, shown with the calculated assumptions.
        </p>
        <button
          className="primary-button calculate"
          disabled={busy}
          type="submit"
        >
          {busy ? (
            <LoaderCircle size={15} className="spin" />
          ) : (
            <Calculator size={15} />
          )}
          {busy ? "Calculating" : "Calculate scenario"}
        </button>
      </form>
      {error && (
        <div role="alert" className="scenario-error">
          <CircleAlert size={15} />
          {error}
        </div>
      )}
      {result && (
        <section className="scenario-result" aria-label="Calculated scenario">
          <h3>Scenario result</h3>
          <dl className="scenario-metrics">
            <div>
              <dt>IRR</dt>
              <dd>
                {result.irr === null
                  ? "Not defined"
                  : `${(result.irr * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`}
              </dd>
            </div>
            <div>
              <dt>Equity multiple</dt>
              <dd>{result.equity_multiple.toFixed(2)}x</dd>
            </div>
            <div>
              <dt>Initial equity</dt>
              <dd>{currency(result.initial_equity)}</dd>
            </div>
          </dl>
          <button className="text-button" type="button" onClick={onAsk}>
            <MessageSquare size={15} />
            Ask about this model
          </button>
          <div
            className="cashflow-table"
            tabIndex={0}
            role="region"
            aria-label="Annual cash flows"
          >
            <table>
              <caption>Annual cash flows / USD</caption>
              <thead>
                <tr>
                  <th>Year</th>
                  <th>NOI</th>
                  <th>Debt service</th>
                  <th>Operating CF</th>
                  <th>Equity CF</th>
                </tr>
              </thead>
              <tbody>
                {result.annual_cash_flows.map((row) => (
                  <tr key={row.year}>
                    <th scope="row">{row.year}</th>
                    <td>{currency(row.noi)}</td>
                    <td>{currency(row.debt_service)}</td>
                    <td>{currency(row.operating_cash_flow)}</td>
                    <td>{currency(row.equity_cash_flow)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="assumptions">
            <summary>Calculated assumptions</summary>
            <dl>
              {Object.entries(result.assumptions).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{assumptionValue(key, value)}</dd>
                </div>
              ))}
            </dl>
          </details>
          <p className="scenario-note">
            {result.basis ||
              "Calculated from user-supplied assumptions. Not verified property financials."}
          </p>
          {result.exclusions?.length ? (
            <p className="scenario-note">
              Excludes: {result.exclusions.join(", ")}.
            </p>
          ) : null}
        </section>
      )}
    </div>
  );
}
