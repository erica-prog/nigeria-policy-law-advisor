# Jurisdiction guides

One folder per country the platform covers, in the layout ADR-001 adopted from the
MIT-licensed `legal-ai-skills` plugin: `authoritative-sources.md` (where primary law is
published and how to verify it), `operating-rules.md` (how an agent must reason in that
jurisdiction), `integrations.md` (connectors and their licence terms) and `skill-map.yaml`
(which playbooks and skills are scoped to it).

These are the human-readable companions to the machine-read taxonomy in
`src/policy_advisor/jurisdictions/data/`. Ids used here (`ng-federal`, `ng-fhc`, ...) are
the taxonomy's ids. The United States folder is adapted from the plugin's `jurisdictions/us/`
guide (Phase 2, ADR-001 action 11); the Nigeria folder is authored here.

**Review status:** not yet reviewed by a practising lawyer. Until it is, agents must say so
when they rely on it, and must not present a source listed here as verified without having
retrieved it.
