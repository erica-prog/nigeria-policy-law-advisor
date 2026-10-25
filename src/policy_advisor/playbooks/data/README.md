# Playbooks

One YAML file per practice area and country (docs/29-playbooks-and-routing.md).
`practice_area` and `country` must exist in `jurisdictions/data/taxonomy.json`;
`allowed_tools` are patterns the tool policy intersects with the registry (a
playbook can only narrow what a lawyer signed off). Checklist and guardrail text
in the investigations playbooks is adapted from the MIT-licensed
`legal-ai-skills` plugin (Rohas Nagpal; commit `ad5df19`), skills
`investigation-report-drafter`, `evidence-organizer`, `fraud-pattern-analyst` and
`whistleblower-report-analyst`; each file carries the attribution. The
immigration playbooks are written new for this project.
