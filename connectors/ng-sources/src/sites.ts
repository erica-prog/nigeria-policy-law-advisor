/**
 * The official Nigerian sites this connector reads, and nothing else.
 *
 * All three publish through WordPress, whose REST API (`/wp-json/wp/v2/`) is
 * the site's own machine interface: searching and reading through it is not
 * scraping, and each site's robots file allows it. NigeriaLII is deliberately
 * absent: its robots file carries `Content-Signal: ai-input=no`, its terms
 * forbid scraping and licence content CC BY-NC (docs/27). The National
 * Assembly site renders its listings by script with no data endpoint found,
 * so it stays reachable through web search only.
 *
 * TypeScript note: `as const` freezes the object so its keys become a literal
 * type (`SiteKey` is exactly "ndpc" | "nis" | "fhc"), which Zod and the
 * compiler both use to reject any other site name.
 */
export const SITES = {
  ndpc: {
    host: "ndpc.gov.ng",
    name: "Nigeria Data Protection Commission",
    body: "ng-ndpc",
    sourceId: "ng-ndpc",
  },
  nis: {
    host: "immigration.gov.ng",
    name: "Nigeria Immigration Service",
    body: "ng-nis",
    sourceId: "ng-nis",
  },
  fhc: {
    host: "fhc.gov.ng",
    name: "Federal High Court of Nigeria",
    body: "ng-fhc",
    sourceId: "ng-fhc",
  },
} as const;

export type SiteKey = keyof typeof SITES;
export const SITE_KEYS = Object.keys(SITES) as SiteKey[];

export function siteForHost(host: string): SiteKey | undefined {
  const bare = host.toLowerCase().replace(/^www\./, "");
  return SITE_KEYS.find((key) => SITES[key].host === bare);
}

/** Every result carries the same provenance envelope the US connectors use. */
export interface Envelope {
  source: string;
  source_url: string;
  retrieved_at: string;
  coverage_warning: string;
  licence: string;
}

export const LICENCE_NOTE =
  "Official Nigerian government publication. Store snippets and the URL; " +
  "confirm the text against the page before quoting it (docs/24 registry row).";

export function envelope(site: SiteKey, url: string, warning: string): Envelope {
  return {
    source: SITES[site].name,
    source_url: url,
    retrieved_at: new Date().toISOString(),
    coverage_warning: warning,
    licence: LICENCE_NOTE,
  };
}
