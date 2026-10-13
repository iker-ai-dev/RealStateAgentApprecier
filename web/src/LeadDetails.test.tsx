import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LeadDetails } from "./LeadDetails";
import { leadSchema, type Lead } from "./api";

const url = "https://www.loopnet.com/search/strip-malls/san-diego-county-ca/for-sale/";
const source: Lead = {
  id: "directory",
  title: "Strip Malls for Sale in San Diego County, CA",
  url,
  summary: '{"mainEntity":{"itemListElement":[]}}',
  properties: [
    {
      id: "university",
      title: "6172-6196 University Ave, San Diego, CA 92115",
      url,
      summary: "20,934 SF Retail Building Offered at $6,000,000 at a 7.25% Cap Rate",
      facts: {
        asking_price: { value: 6000000, unit: "USD", quote: "Offered at $6,000,000", source_url: url, status: "source_reported" },
        area_sqft: { value: 20934, unit: "sqft", quote: "20,934 SF", source_url: url, status: "source_reported" },
        cap_rate: { value: 7.25, unit: "%", quote: "7.25% Cap Rate", source_url: url, status: "source_reported" },
      },
    },
    {
      id: "kettner",
      title: "2483 Kettner Blvd, San Diego, CA 92101",
      url,
      summary: "1,610 SF Retail Building Offered at $1,050,000",
      facts: {
        asking_price: { value: 1050000, unit: "USD", quote: "Offered at $1,050,000", source_url: url, status: "source_reported" },
        area_sqft: { value: 1610, unit: "sqft", quote: "1,610 SF", source_url: url, status: "source_reported" },
      },
    },
  ],
};

describe("directory property facts", () => {
  it("opens a property with available figures instead of an empty first entry", () => {
    render(<LeadDetails lead={{ ...source, properties: [
      { id: "empty", title: "Call for details", url, summary: "Call for details", facts: {} },
      ...source.properties!,
    ] }} />);
    expect(screen.getByRole("combobox")).toHaveValue("university");
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "empty" } });
    expect(screen.getByRole("combobox")).toHaveValue("empty");
    expect(screen.queryByText("6,000,000")).toBeNull();
  });

  it("keeps extracted properties through API parsing and never mixes their metrics", () => {
    const parsed = leadSchema.parse(source);
    const { container } = render(<LeadDetails lead={parsed} />);
    const metric = (label: string) => screen.getByText(label, { selector: "dt" }).parentElement!;
    expect(within(metric("Asking price")).getByText("6,000,000")).toBeVisible();
    expect(metric("Area")).toHaveTextContent("20,934");
    expect(metric("Cap rate")).toHaveTextContent("7.25");
    expect(screen.queryByText(source.summary!)).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Property from source" }), { target: { value: "kettner" } });
    expect(metric("Asking price")).toHaveTextContent("1,050,000");
    expect(metric("Area")).toHaveTextContent("1,610");
    expect(metric("Cap rate")).toHaveTextContent("Not provided");
    expect(metric("Annual NOI")).toHaveTextContent("Not provided");
    expect(container).not.toHaveTextContent("6,000,000");
    expect(container).not.toHaveTextContent("7.25");
  });

  it("retains source provenance and hides the wall of empty metrics for empty sources", () => {
    const { rerender } = render(<LeadDetails lead={source} />);
    expect(screen.getAllByRole("link", { name: "Fact source" })).toHaveLength(3);
    for (const link of screen.getAllByRole("link", { name: "Fact source" })) expect(link).toHaveAttribute("href", url);
    rerender(<LeadDetails lead={{ id: "empty", title: "Background article", url, summary: "No financial data supplied." }} />);
    expect(screen.getByText("No property-level figures could be extracted from the saved source.")).toBeVisible();
    expect(screen.queryByText("Not provided")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("falls back to the new source's first property when the source changes", () => {
    const { rerender } = render(<LeadDetails lead={source} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "kettner" } });
    rerender(<LeadDetails lead={{ ...source, id: "other", properties: [source.properties![0]] }} />);
    expect(screen.getByRole("combobox")).toHaveValue("university");
    expect(screen.getByRole("heading", { name: source.properties![0].title })).toBeVisible();
  });
});
