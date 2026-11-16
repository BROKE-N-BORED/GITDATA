# Changelog

## v0.1.3 (2026-11-15)

All 67 items from the v0.1.2 audit, in one release.

- T1: Converted outputs keep the source folder in the name, so two files called train.jsonl no longer overwrite each other, and the row count counts only rows actually on disk
- T2: The output folder is unique per dataset, not per title, so two datasets with the same title no longer clobber each other
- T3: Link-check and STELLA-ready results match items by URL, not by position, and checks refuse to let a remove or new search move the ground under them
- T4: A corrupt list file recovers from the .bak backup instead of opening empty, and the app says what was recovered
- T5: A rate-limit reply during sniffing or the update check is a plain "unknown", never a crash or a hang
- T6: A 403 now says what it really is (Hugging Face, a private repo, or a real rate limit) instead of always blaming the GitHub rate limit
- T7: Truncated reads and malformed replies are caught in the network layer, and a dead source is reported instead of shown as a success
- T8: A short download is refused instead of treated as complete
- T9: One bad file no longer kills a whole conversion; the good files still convert and every output on disk appears in the report
- T10: The 200 MB data-file cap and the 500 MB archive cap are enforced before downloading, in every branch
- T11: A failed save tells the user in plain words, and closing the window saves the list first
- T12: The search cache is validated on load and eviction survives damaged entries, the way the list file was hardened
- T13: A GUI save no longer clobbers changes made via the CLI while the GUI was open; it merges instead
- T14: The rate meter shows the search quota and its reset time, and the copy says per minute, which is the real GitHub window
- T15: The sniff tooltip shows the true dataset size instead of Hugging Face's wrong usedStorage
- T16: Closing mid-link-check keeps the statuses checked so far
- T17: The Add and Check buttons wait while a search runs, so Add all can no longer add the previous search's results
- T18: The STELLA check has a Stop button and a "Checking 3 of 12" count, like the link check
- T19: Removing a website or restoring the defaults asks first (default No), and a removed website can be undone
- T20: A HEAD 404 falls back to GET before a link is called dead, so live Kaggle pages are no longer reported dead
- T21: Converted .jsonl files, exports, and the STELLA manifest are written atomically, so a crash mid-write never leaves a truncated file
- T22: Load more fetches the next page of search results instead of the hard 30 plus 30 cap
- T23: The double-click "Install Dataset Scout" launcher the README promises is in the zip, and the installer's false resume claim is fixed
- T24: URL dedup treats http and https as the same link, and a bare host as the root path
- T25: A result's description and topics survive being saved to My List
- T26: A Parquet-only dataset sniffs as convertible only when its reader is installed, and a failed peek is "unknown"
- T27: In-app downloads are resumable: a failed download picks up from its .part file instead of starting over
- T28: Download Stop interrupts the file downloading now instead of waiting for it to finish
- T29: Extraction is capped by total extracted size and member count, so a small archive cannot expand to many GB
- T30: The converter falls through to a mapping that works when the prompt or messages field is empty or broken
- T31: The Edit dialog no longer renames a category when the user saves without touching it
- T32: CLI search results sort newest-first, like the GUI
- T33: A headerless CSV is detected instead of losing its first data row to the header
- T34: Compressed files like .jsonl.gz are named honestly instead of the app claiming no convertible files exist
- T35: Imports read with utf-8-sig, so a BOM from a Windows editor no longer breaks them
- T36: Renaming a collection saves the active list before the rename, not after
- T37: Newest-first sorts by parsed time, not by raw date string, so timezones no longer misorder results
- T38: The zip-slip guard is airtight on its own instead of leaning on zipfile's sanitisation
- T39: Field explanations under the My List tab and Edit dialog fields
- T40: Docs pass: em-dashes out of the README and CHANGELOG, and the built-ins count fixed
- T41: "Pick rows and add them" no longer appears when a search finds nothing
- T42: scout-cli --self-test exercises search, sniff, and a sample conversion end to end
- T43: Small sweep: dead token parameter, stale .jsonl cleanup, honest state on total failure, unrecognised state kept, CLI limit clamped, licence case, prerelease compare, id cleaning, GitHub sniff size
- T44: Visible gold focus ring on buttons, fields, and tables
- T45: Shortcuts are discoverable: a "?" overlay built from the shortcuts registry, keys shown in menus and tooltips
- T46: A search that finds nothing says so and suggests broadening, instead of repeating the first-run hint; the Sources table has an empty state
- T47: Verb-first tooltips on the action buttons and menu items
- T48: Forms validate inline, on blur, beside the field
- T49: My List is in a scroll area with its buttons in three collapsible sections
- T50: A detail pane shows the full description, files, sizes, and license for a result
- T51: The site buttons wrap instead of pushing the Search tab wider than the window
- T52: The skeleton fills every column, and long searches show elapsed time beside Stop
- T53: Density control for the two tables: Comfortable (default) or Compact, persisted
- T54: How to Use has a clickable index of its sections
- T55: One link-health bar on the stats strip with a plain takeaway
- T56: A first-run setup checklist: search, save a link, optional token
- T57: A small finish signal when a link check, download, or STELLA check completes
- T58: Text sizes come from the system font, so GNOME Large Text works
- T59: Dialog buttons say what happens ("Add link", "Save changes"), and the copy no longer hard-codes a count of five categories
- T60: Completion popups queue behind open modals instead of stacking on top of them
- T61: The Sources table's Remove column is labelled
- T62: Parquet datasets convert natively with an optional pyarrow reader (lazy import, so the app still runs without it)
- T63: Dataset watch: opt a saved search in and Scout flags what is new since the last run (off by default, in-app only)
- T64: A converted dataset can go straight into a STELLA project, with the project backed up first
- T65: A plain-words quality score on each result, from activity, popularity, license, and STELLA shape
- T66: A bulk download queue with pause and resume across sessions
- T67: Offline mode: browse cached results and convert local files with an honest "you are offline" note

## v0.1.2 (2026-11-13)

The audit build: all 35 items from the v0.1.1 audit task list, in
one release. T31 (Papers With Code search) is done by removal:
Papers With Code shut down entirely when Hugging Face bought it:
the site and the API both redirect to Hugging Face now, so the
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
