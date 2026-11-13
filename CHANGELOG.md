# Changelog

## v0.1.1 (2026-11-13)

Design-rules compliance patch, before first ship. No new features.

- No em-dashes in visible UI text (U33): status messages, the
  site-address placeholder, and the STELLA format note rewritten in
  plain words; a test sweeps visible text for em-dashes
- Help menu with a How to Use window: a plain-words index covering
  Search, Categories and filters, Websites, My List, Check links,
  Export for STELLA, GitHub token, and Debug report (U22)
- Single instance (U27): re-clicking the icon raises the running
  window instead of starting a second copy; a stale lock from a
  crashed run is taken over cleanly. The CLI is unaffected
- Window remembers its position and size between runs, default
  1100x700 (U5)
- A busy dot in the status bar while a search or link check runs
  (U16); the status bar stays the only status text
- Link check shows progress ("Checking 3 of 12...") and a Stop
  button; stopping keeps the links already checked (U26)
- Search now tries both sources even if one fails, and says in the
  status bar which source did not answer (U32); the error dialog
  appears only when both fail

## v0.1.0 (2026-11-13)

First release. Dataset Scout finds training datasets for STELLA and
builds lists of links.

- Live GitHub repo search (API, optional token stored only on this PC)
  and live Hugging Face datasets search (no key needed)
- Category chips: All, Post-training, Instruction tuning, Reasoning,
  Training datasets
- Active-only filter (3/6/12/24 months, or any age) and min-stars filter
- Custom websites: add or remove, with search-on-site buttons and an
  "Add a link I found" form
- My List: save links, check them (working/dead/unknown), copy them all
- Export: Markdown list, JSON, and STELLA manifest (carries the plain-words
  note that downloads must be converted to STELLA's .jsonl shape)
- Debug log layer with a scrubbed report (the token never appears in a log)
- GUI double-click installer with menu and desktop icon
- Command line: `scout search`, `scout sources`, `scout list`, `scout export`
