# Changelog

## v0.1.2 (2026-11-13)

The audit build: all 35 items from the v0.1.1 audit task list, in
one release. T31 (Papers With Code search) is done by removal:
Papers With Code shut down entirely when Hugging Face bought it —
the site and the API both redirect to Hugging Face now — so the
dead default entry is gone and Hugging Face search covers the
same content.

- T0: Categories are data, not a hardcoded list: the Markdown export
  now gives every category present its own section instead of
  dropping items whose category is not one of the five search
  presets, and My List shows an unknown category's own label
  instead of "All"
- T1: Bad rows in the saved list and bad numbers from the search
  results no longer break the app: a broken row is skipped and
  logged, a bad number reads as 0
- T2: Saves are atomic: a crash mid-save can no longer leave a
  half-written list. A backup of the previous list is kept too
- T3: Closing the window stops the running searches and link checks
  instead of leaving them behind
- T4: Search results are newest first, as the README said they
  were, instead of GitHub's order followed by Hugging Face's
- T5: Hugging Face results carry the real license, format, and size
  from their tags instead of blanks
- T6: The JSON and STELLA exports carry download counts, formats,
  and sizes alongside the GitHub stars
- T7: The same link with or without a trailing slash, www, or a
  fragment is now one link, not two
- T8: Old debug logs are cleaned up: logs older than a week are
  deleted when the app starts
- T9: Filter transparency: the results count now says how many
  results the filters hid, so "Found 0" never looks like the
  search found nothing when the filters did it
- T10: Search runs GitHub and Hugging Face at the same time, and
  link checks run 4 at a time, so both finish faster
- T11: Result and list tables sort by clicking a column heading,
  and sort by number and date instead of alphabet
- T12: A link in My List can be edited in place: title, category,
  notes, and state
- T13: Lists can be imported back: Markdown, JSON, and STELLA
  manifests, from the menu or the command line
- T14: Repeated searches are answered from a one-hour cache, and
  the last 10 searches are kept as history in the search box
- T15: The stars filter says plainly that it counts Hugging Face
  likes as well as GitHub stars
- T16: The status bar shows how many GitHub searches are left
  before the hourly limit
- T17: A link check updates only the row it just checked instead
  of rebuilding the whole table
- T18: Removing a link can be undone, and removal asks first
- T19: Keyboard shortcuts: Ctrl+F focuses the search box, Ctrl+E
  exports for STELLA, Delete removes a selected link. My List has
  a text filter and a state filter
- T20: The command line does more: add and remove links, check
  links, import a list, check for updates; a failed source no
  longer kills the whole search
- T21: Help has a Check for updates item, and the command line has
  update-check; both compare against the latest GitHub release
- T22: The Search tab is one scrollable page, and the hand-add form
  is a dialog, so the window is calmer at small sizes
- T23: Real menus: File (export, import, Send to STELLA, exit),
  Edit (undo, remove, clear), View (the three tabs), and Help
- T24: Dark theme from the app icon: deep navy, gold, and light
  blue, with one stylesheet and a stylesheet-first menu
- T25: Empty states and a live stats strip: an empty list or
  empty search says what to do next, and the status bar shows
  links, working, dead, and searches run
- T26: The form fields explain themselves in plain words
- T27: Honest loading states: a search shows loading rows while
  it runs instead of a blank table
- T28: Row actions and drag and drop: right-click a row in My
  List for Open, Edit, Copy link, Mark as, or Remove, and drag
  rows to reorder them, with the order saved
- T29: STELLA-ready check: per link, on demand, Scout looks at the
  dataset's real files and says whether STELLA can use them, needs
  conversion, or there is nothing to convert
- T30: My List has named collections: keep several lists, switch
  between them, rename them, delete them. Old lists migrate
  on their own
- T31: Papers With Code is gone entirely (site + API both redirect
  to Hugging Face since the acquisition), so there is no live search
  to add. Done by removal: the dead default entry is gone, a saved
  entry for it is dropped on load, and Hugging Face search covers
  the same content. Nothing was faked in its place
- T32: A license filter on search: keep only results with a chosen
  license, such as Apache 2.0 or MIT
- T33: Send to STELLA: writes the manifest into STELLA's home
  folder when STELLA is installed on this PC
- T34: The installer retries a failed download up to 3 times and
  shows progress; a retry picks up from the cache
- T35: Download and convert: selected links are downloaded and
  their JSON, JSONL, CSV, and TSV files converted into STELLA
  .jsonl, in a stella-ready folder. Parquet and Arrow are named
  as unsupported, never pretended. Rows that cannot become a
  prompt and completion pair are skipped, not faked

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
