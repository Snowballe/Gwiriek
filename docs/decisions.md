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
| 2026-11-19 | Inspectors are a separate model linked to `Person` for internal ones | External qualified persons are not company staff; one list covers R4323-100 for both. |
| 2026-11-19 | An inspector must be qualified on the inspection day and on the recording day | Prevents backdating around an expired authorization; a real inspection refused this way leads to a new inspection, the safe side. |
| 2026-11-19 | Checklist points and inspector identity are frozen into each inspection | Catalogue or inspector edits must not rewrite the safety register. |
| 2026-11-19 | Inspections are append-only, enforced in the model layer; corrections are new inspections, not dated before the latest one | Same traceability as the status log; the latest inspection stays the one that sets the deadline. |
| 2026-11-19 | A non-compliant point allows only quarantine or retire | "Under watch" keeps the item in use, which a failed check against the manufacturer's procedure does not allow; acceptable wear is recorded as compliant with a comment. |
| 2026-11-19 | Every checklist point needs a result; without a checklist a comment is required | A partial or empty record does not prove the item was checked. |
| 2026-11-19 | Next inspection: model interval (at most 12 months), earlier on request, capped by the end of life, none after retirement | Arrêté of 19 March 1993: inspected less than 12 months before use and disposed of in due time. |
| 2026-11-19 | A never-inspected item counts from the earlier of its date put into service and purchase date; with neither, it is due at once | The arrêté covers stock too; the registration date or a missing date would hide items never checked. |
| 2026-11-19 | A "watch" decision on an item without a date put into service sets it to the inspection date | Same rule as `change_status`; it starts the service-life clock early, the safe side. |
| 2026-11-19 | One due report merges end of life and inspection, with the nearest deadline per item | A single list to act on; `due --check` exit codes unchanged. |
| 2026-11-19 | E-mail digest deduplicated per day by a content fingerprint stored in `DigestLog` | A cron run twice does not spam; a changed situation is sent again the same day. |
| 2026-11-19 | Django `MAILERS` setting, console backend by default | `EMAIL_*` settings are deprecated in Django 6.1; nothing is sent by mistake from a development setup. |
| 2026-11-19 | Migration 0002 recreates the inspection table and stops if it holds rows | The 0001 table had no workflow writing to it; its inspector fields are replaced by a foreign key to `Inspector`. |
| 2026-11-19 | An item counts as put into use only with a service date or an `in_service`/`watch` status | A stock item sent to quarantine or retired without a service date keeps its storage limit; treating it as in use would push its end of life back. |
