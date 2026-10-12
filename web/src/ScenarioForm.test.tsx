import { expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScenarioForm, scenarioInput } from "./ScenarioForm";
import { api } from "./api";

it("converts percentage display to decimal API rates and omits blank optional inputs", () => {
  expect(
    scenarioInput({
      purchase_price: "1000000",
      annual_noi: "100000",
      exit_cap_rate: "6",
      hold_years: "7",
      loan_to_value: "65",
      interest_rate: "5.5",
      noi_growth: "",
    }),
  ).toEqual({
    purchase_price: 1000000,
    annual_noi: 100000,
    exit_cap_rate: 0.06,
    hold_years: 7,
    loan_to_value: 0.65,
    interest_rate: 0.055,
  });
  expect(() =>
    scenarioInput({
      purchase_price: "",
      annual_noi: "0",
      exit_cap_rate: "6",
      hold_years: "7",
      loan_to_value: "",
      interest_rate: "",
      noi_growth: "",
    }),
  ).toThrow("Purchase price is required");
  expect(() =>
    scenarioInput({
      purchase_price: "1",
      annual_noi: "1",
      exit_cap_rate: "0",
      hold_years: "7",
      loan_to_value: "",
      interest_rate: "",
      noi_growth: "",
    }),
  ).toThrow("valid exit cap rate");
});

it("starts required financial inputs blank and renders only server-calculated metrics", async () => {
  const scenario = vi
    .spyOn(api, "scenario")
    .mockResolvedValue({
      irr: null,
      initial_equity: 1000000,
      equity_multiple: 1.1,
      annual_cash_flows: [
        {
          year: 1,
          noi: 100000,
          debt_service: 0,
          operating_cash_flow: 100000,
          equity_cash_flow: 1100000,
        },
      ],
      assumptions: {
        purchase_price: 1000000,
        exit_cap_rate: 0.06,
        hold_years: 7,
        loan_to_value: 0,
      },
    });
  const onCalculated = vi.fn();
  const onInvalidate = vi.fn();
  const onAsk = vi.fn();
  render(
    <ScenarioForm
      onCalculated={onCalculated}
      onInvalidate={onInvalidate}
      onAsk={onAsk}
    />,
  );
  expect(screen.getByLabelText(/Purchase price/)).toHaveValue(null);
  expect(screen.getByLabelText(/Annual NOI/)).toHaveValue(null);
  expect(screen.getByLabelText(/Exit cap rate/)).toHaveValue(null);
  expect(screen.getByLabelText("Hold period")).toHaveValue(7);
  expect(screen.queryByText("IRR")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Purchase price/), {
    target: { value: "1000000" },
  });
  fireEvent.change(screen.getByLabelText(/Annual NOI/), {
    target: { value: "100000" },
  });
  fireEvent.change(screen.getByLabelText(/Exit cap rate/), {
    target: { value: "6" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Calculate scenario" }));
  await screen.findByText("Not defined");
  expect(scenario).toHaveBeenCalledWith(
    {
      purchase_price: 1000000,
      annual_noi: 100000,
      exit_cap_rate: 0.06,
      hold_years: 7,
    },
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask about this model" }));
  expect(onAsk).toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText(/Annual NOI/), {
    target: { value: "120000" },
  });
  await waitFor(() =>
    expect(screen.queryByText("IRR")).not.toBeInTheDocument(),
  );
  expect(onInvalidate).toHaveBeenCalled();
});

it("surfaces scenario API errors without displaying metrics", async () => {
  vi.spyOn(api, "scenario").mockRejectedValue(new Error("Invalid assumptions"));
  render(
    <ScenarioForm
      onCalculated={() => {}}
      onInvalidate={() => {}}
      onAsk={() => {}}
    />,
  );
  for (const [label, value] of [
    [/Purchase price/, "1000000"],
    [/Annual NOI/, "100000"],
    [/Exit cap rate/, "6"],
  ] as const)
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Calculate scenario" }));
  await screen.findByText("Invalid assumptions");
  expect(screen.queryByText("IRR")).toBeNull();
});
