import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { api } from "./api";
import { ProviderConnections } from "./ProviderConnections";

const status = { providers: { snowflake: true, nimble: false }, model: "test" };
it("does not claim configured credentials are connected and checks only on request", async () => {
  const check = vi.spyOn(api, "checkProviders").mockResolvedValue({ providers: {
    snowflake: { state: "connected", checked_at: "2026-10-14T00:00:00Z", message: "Query succeeded" },
    nimble: { state: "error", checked_at: "2026-10-14T00:00:00Z", message: "Connection failed" },
  } });
  render(<ProviderConnections status={status} />);
  expect(screen.getByLabelText(/Snowflake: Configured/)).toBeVisible();
  expect(screen.getByLabelText(/Nimble: Not configured/)).toBeVisible();
  expect(check).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check provider connections" }));
  expect(await screen.findByLabelText(/Snowflake: Connected/)).toBeVisible();
  expect(screen.getByLabelText(/Nimble: Unavailable/)).toBeVisible();
});
it("keeps failures visible without showing a verified connection", async () => {
  vi.spyOn(api, "checkProviders").mockRejectedValue(new Error("Check timed out"));
  render(<ProviderConnections status={status} />);
  fireEvent.click(screen.getByRole("button", { name: "Check provider connections" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Check timed out");
  expect(screen.getByLabelText(/Snowflake: Configured/)).toBeVisible();
});
