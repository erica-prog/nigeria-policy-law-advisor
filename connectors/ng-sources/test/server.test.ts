/**
 * Fixture-driven tests: a fake `fetch` answers from recorded REST responses,
 * so nothing touches the network. Run with `npm test`.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { citation, formatDate } from "../src/cite.js";
import { createServer } from "../src/server.js";
import { SITES, siteForHost } from "../src/sites.js";
import { fetchPage, htmlToText, searchSite, slugOf, type FetchLike } from "../src/wordpress.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = (name: string) => readFileSync(join(here, "fixtures", name), "utf8");

/** Routes a URL to a fixture; anything else is a 404. */
const fakeFetch: FetchLike = async (input) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  const respond = (body: string, status = 200) =>
    new Response(body, { status, headers: { "content-type": "application/json" } });
  if (url.startsWith("https://immigration.gov.ng/wp-json/wp/v2/search")) return respond(fixture("nis_search_visa_on_arrival.json"));
  if (url.startsWith("https://fhc.gov.ng/wp-json/wp/v2/search")) return respond(fixture("fhc_search_practice_direction.json"));
  if (url.startsWith("https://ndpc.gov.ng/wp-json/wp/v2/search")) return respond("[]");
  if (url.startsWith("https://immigration.gov.ng/wp-json/wp/v2/pages?slug=expired-visa-holders")) return respond(fixture("nis_page_expired_visa_holders.json"));
  if (url.includes("/wp-json/wp/v2/pages?slug=") || url.includes("/wp-json/wp/v2/posts?slug=")) return respond("[]");
  return respond("not found", 404);
};

describe("wordpress reader", () => {
  it("maps search results to hits with the page/post kind", async () => {
    const hits = await searchSite(fakeFetch, "nis", "visa on arrival", 3);
    expect(hits).toHaveLength(3);
    expect(hits[1]).toMatchObject({ site: "nis", kind: "page", title: "Regularize your visa" });
    expect(hits[0].kind).toBe("post");
    expect(hits.every((h) => h.url.startsWith("https://immigration.gov.ng/"))).toBe(true);
  });

  it("reads a page by slug and reduces its HTML to text", async () => {
    const page = await fetchPage(fakeFetch, "nis", new URL("https://immigration.gov.ng/expired-visa-holders/"));
    expect(page.id).toBe(3612);
    expect(page.title).toBe("Regularize your visa");
    expect(page.text).not.toMatch(/<[a-z]+[^>]*>/i);
    expect(page.text.length).toBeGreaterThan(20); // the fixture keeps only the first 2,500 characters of HTML
    expect(page.published).toBe("2025-04-12T16:37:23");
  });

  it("refuses slugs it cannot read and unknown pages", async () => {
    await expect(fetchPage(fakeFetch, "ndpc", new URL("https://ndpc.gov.ng/no-such-page/"))).rejects.toThrow(/no page or post/);
    expect(slugOf(new URL("https://ndpc.gov.ng/a/b/"))).toBe("b");
  });

  it("decodes entities and keeps paragraph breaks", () => {
    expect(htmlToText("<p>Visa &amp; permit</p><p>Second &#8217;line&#8217;</p>")).toBe("Visa & permit\nSecond ’line’");
  });
});

describe("citation", () => {
  it("formats an OSCOLA-style web citation with the accessed date", () => {
    const text = citation({
      site: "nis",
      title: "Regularize your visa",
      url: "https://immigration.gov.ng/expired-visa-holders/",
      date: "2025-04-12T16:37:23",
      accessed: new Date(Date.UTC(2026, 9, 24)),
    });
    expect(text).toBe(
      "Nigeria Immigration Service, 'Regularize your visa' (immigration.gov.ng, 12 April 2025) <https://immigration.gov.ng/expired-visa-holders/> accessed 24 October 2026",
    );
    expect(formatDate("not a date")).toBeUndefined();
  });
});

describe("sites", () => {
  it("knows only the three official hosts", () => {
    expect(siteForHost("www.ndpc.gov.ng")).toBe("ndpc");
    expect(siteForHost("nigerialii.org")).toBeUndefined();
    expect(Object.keys(SITES)).toEqual(["ndpc", "nis", "fhc"]);
  });
});

describe("server tools", () => {
  // The SDK keeps registered tools on a private map; reading it is enough to
  // check what the model will be shown without opening a transport.
  it("registers exactly the three read-only tools", () => {
    const server = createServer(fakeFetch) as unknown as { _registeredTools: Record<string, unknown> };
    expect(Object.keys(server._registeredTools).sort()).toEqual(["cite_ng_official", "fetch_ng_official", "search_ng_official"]);
  });
});
