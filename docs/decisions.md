# Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-11-19 | Dedicated application rather than a GLPI plugin | GLPI's desktop UI, yearly plugin porting and IT-oriented audience fit rope access teams poorly; a GLPI bridge stays possible later. |
| 2026-11-19 | Django | Admin, ORM, migrations and i18n out of the box; Python ecosystem; mature enough for a small team to maintain. |
| 2026-11-19 | Licence AGPL-3.0 | Keeps hosted versions (SaaS) free software as well. |
| 2026-11-19 | Business services + CLI + MCP | All logic in typed services so the admin, the `gwiriek` command and the MCP server stay thin and consistent; scripts and AI assistants get the same guarantees as the web UI. |
| 2026-11-19 | English in code, French UI through Django i18n | Open source project readable by anyone; users are French rope access companies. |
| 2026-11-19 | Django 6.1 | Latest stable release when the core was started. |
| 2026-11-19 | Official MCP Python SDK (`mcp` 2.x), stdio transport | Reference implementation; stdio needs no network exposure. Remote access requires an authenticated HTTP transport first. |
| 2026-11-19 | Argparse for the CLI | Standard library, no extra dependency. |
| 2026-11-19 | Lifespans in months, `null` meaning "not given", explicit "unlimited" flag | Distinguishes missing catalogue data from a manufacturer's stated unlimited lifetime (e.g. Petzl metal products). |
| 2026-11-19 | End of life computed, never stored | Always consistent with the current catalogue; the list filter and the due report compute it in Python, which suits fleets of a few thousand items. |
| 2026-11-19 | Purchase date never replaces the manufacture date | It would give a later, unsafe end of life; missing data is reported instead. |
| 2026-11-19 | Items past their end of life cannot be put into use or issued | The arrêté of 19 March 1993 requires disposal in due time. |
| 2026-11-19 | Append-only status audit log, enforced in the model layer | Traceability of decisions the tool's users are liable for; database-level enforcement (triggers, signatures) can follow. |
| 2026-11-19 | Items cannot be deleted from the admin | Life records are kept 5 years after disposal (D4711-3, Copalme guide); retire instead. |
