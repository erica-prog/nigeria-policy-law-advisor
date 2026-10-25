#!/usr/bin/env node
/**
 * ng-sources: an MCP server over official Nigerian government sites
 * (docs/27-nigeria-connector.md). Three read-only tools: search, fetch, cite.
 *
 * Started by the Python side as a child process over stdio
 * (policy_advisor/tools/client.py). It holds no credentials and reaches only
 * the hosts in sites.ts.
 *
 * TypeScript notes for a Python reader:
 * - `z.object({...})` is a Zod schema: it validates the tool's input at
 *   runtime and the SDK also publishes it as JSON Schema to the model.
 * - `registerTool(name, config, handler)` is the SDK's way to declare a tool;
 *   the handler's `args` are already validated and typed from the schema.
 * - `structuredContent` is the machine-readable result; `content` is the
 *   same thing as text for clients that only read text.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import { citation } from "./cite.js";
import { SITE_KEYS, SITES, envelope, siteForHost, type SiteKey } from "./sites.js";
import { fetchPage, searchSite, searchUrl, type FetchLike } from "./wordpress.js";

export const VERSION = "0.1.0";

const siteSchema = z.enum(SITE_KEYS as [SiteKey, ...SiteKey[]]);

const SEARCH_WARNING =
  "Results come from each site's own search index: pages are official guidance, posts are news " +
  "or announcements. Open the page with fetch_ng_official before relying on its wording.";
const FETCH_WARNING =
  "Text as published on the official site on the retrieval date; the page may change. " +
  "Quote with the URL and the accessed date.";

function textAndStructured(result: Record<string, unknown>) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
    structuredContent: result,
  };
}

function errorResult(message: string) {
  return { content: [{ type: "text" as const, text: message }], isError: true as const };
}

/** Builds the server. `fetchImpl` is injectable so tests use fixtures. */
export function createServer(fetchImpl: FetchLike = fetch): McpServer {
  const server = new McpServer({ name: "policy-advisor-ng-sources", version: VERSION });

  server.registerTool(
    "search_ng_official",
    {
      title: "Search official Nigerian government sites",
      description:
        "Search the Nigeria Data Protection Commission (ndpc), Nigeria Immigration Service (nis) " +
        "and Federal High Court (fhc) websites through their own search. Returns titles and URLs; " +
        "use fetch_ng_official to read a result.",
      inputSchema: {
        query: z.string().min(2).max(200).describe("Words to search for, as a lawyer would type them."),
        site: siteSchema.optional().describe("One site; omit to search all three."),
        limit: z.number().int().min(1).max(20).default(5).describe("Results per site."),
      },
    },
    async ({ query, site, limit }) => {
      const sites: SiteKey[] = site ? [site] : [...SITE_KEYS];
      const hits = [];
      const errors: string[] = [];
      for (const key of sites) {
        try {
          hits.push(...(await searchSite(fetchImpl, key, query, limit)));
        } catch (error) {
          errors.push(`${SITES[key].host}: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      if (hits.length === 0 && errors.length === sites.length) {
        return errorResult(`No site could be searched. ${errors.join("; ")}`);
      }
      const primary = sites[0];
      return textAndStructured({
        ...envelope(primary, searchUrl(primary, query, limit), SEARCH_WARNING),
        source: sites.map((key) => SITES[key].name).join("; "),
        query,
        hits,
        errors,
      });
    },
  );

  server.registerTool(
    "fetch_ng_official",
    {
      title: "Read a page from an official Nigerian government site",
      description:
        "Read one page or post from ndpc.gov.ng, immigration.gov.ng or fhc.gov.ng by its URL. " +
        "Other hosts are refused. Returns the page text (bounded) with its published and modified dates.",
      inputSchema: {
        url: z.string().url().describe("The page URL, as returned by search_ng_official."),
      },
    },
    async ({ url }) => {
      const parsed = new URL(url);
      const site = siteForHost(parsed.hostname);
      if (!site || parsed.protocol !== "https:") {
        return errorResult(
          `Refused: ${parsed.hostname} is not one of the official sites this connector reads ` +
            `(${SITE_KEYS.map((k) => SITES[k].host).join(", ")}).`,
        );
      }
      try {
        const page = await fetchPage(fetchImpl, site, parsed);
        return textAndStructured({
          ...envelope(site, page.url, FETCH_WARNING),
          ...page,
        });
      } catch (error) {
        return errorResult(error instanceof Error ? error.message : String(error));
      }
    },
  );

  server.registerTool(
    "cite_ng_official",
    {
      title: "Cite a page from an official Nigerian government site",
      description:
        "Format a citation for a page on one of the official sites: author, title, site and date, " +
        "URL, accessed date. Use this instead of writing the citation yourself.",
      inputSchema: {
        site: siteSchema,
        title: z.string().min(1).max(300),
        url: z.string().url(),
        date: z.string().optional().describe("Published or modified date from fetch_ng_official, if known."),
      },
    },
    async ({ site, title, url, date }) => {
      const host = new URL(url).hostname;
      if (siteForHost(host) !== site) {
        return errorResult(`Refused: ${host} does not belong to site ${site}.`);
      }
      return textAndStructured({
        ...envelope(site, url, "Citation formatted from the metadata you supplied; verify the date on the page."),
        citation: citation({ site, title, url, date }),
      });
    },
  );

  return server;
}

async function main(): Promise<void> {
  const server = createServer();
  await server.connect(new StdioServerTransport());
}

// Run only when executed directly, not when imported by tests.
const invokedDirectly = process.argv[1] !== undefined && import.meta.url === new URL(`file://${process.argv[1]}`).href;
if (invokedDirectly || process.env.NG_SOURCES_RUN === "1") {
  main().catch((error) => {
    process.stderr.write(`ng-sources failed: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
