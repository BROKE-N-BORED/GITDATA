# Changelog

## v0.1.4 (2026-11-17)

All 65 items from the v0.1.3 user-experience review, in one release.

- T1: "Convert a file on this PC" keeps the folder in the output name, so two files called train.jsonl no longer overwrite each other
- T2: The STELLA Replace checkbox compares file contents, so a different dataset is never silently swapped in with Replace unchecked
- T3: A link added by hand gets its source from the URL, so pasted Hugging Face and GitHub links can be checked and downloaded
- T4: The installer now installs the Parquet reader (pyarrow, about 169 MB on disk), so Parquet datasets convert on a real install; the reader stays optional and loads only when needed
- T5: An empty search box or watch says "Type a few words first" and does nothing, instead of silently searching for "dataset"
- T6: A rejected GitHub token is named on screen, with the fix, instead of only in the log
- T7: "Copy debug report" includes the on-disk log tail, so a report from a previous session is not empty
- T8: No red errors show after a website is added successfully
- T9: The installer prints the real PySide6 import error instead of guessing network or disk space
- T10: Parquet datasets are peeked for usable columns before the badge promises a conversion
- T11: Sniffing a repo peeks past the first .jsonl instead of judging one file
- T12: "Checked, no usable fields" has its own status instead of "Check failed"
- T13: The score cell refreshes when the STELLA badge lands, so the two columns no longer contradict each other
- T14: The queue log no longer claims a normal run was stopped, and "Downloading" shows during a run
- T15: A queue run marks the My List row converted, like a single download does
- T16: A download run that converts nothing shows a plain "Nothing converted" state, no checkmark and no full bar
- T17: The STELLA check summary counts ready, convertible, and failed instead of only the ready count
- T18: An offline search with no cached results clears the stale rows instead of leaving the previous search under the new query
- T19: prompt+response and question+ground_truth rows map, and the skip reason says which field is actually missing
- T20: A dataset STELLA cannot use can no longer score "Worth a look" or "Promising"
- T21: A junk token gets one honest message instead of a success line plus a red error
- T22: A link that fails the app's own check is not added to My List
- T23: A failed search says so in one plain sentence, no error code twice
- T24: Import and export failures are plain sentences, no raw errors or full paths
- T25: An over-cap file is refused from its declared size before downloading, in every branch
- T26: Blanking a title no longer claims success while silently keeping the old one
- T27: Link-check Stop drops the in-flight check at once and counts only what was actually checked
- T28: The STELLA format note is plain words instead of raw schema
- T29: After a failed search the label says the search failed instead of the never-searched text
- T30: Small wrongs swept: installer size says about 200 MB, report messages name the folder, queue intro corrected, image-only downloads name what the dataset holds, "Open the folder" stays disabled after a failed run
- T31: The Search tab fits at 1100x700: results, Add buttons, Load more, and the STELLA and Score columns all visible without scrolling
- T32: The My List Title column is capped so State, STELLA, and Score stay visible at 1366 wide
- T33: The status text gets priority over the permanent status-bar widgets, so messages are no longer clipped
- T34: "Clear all" on an empty list says there is nothing to clear
- T35: Answering No to a confirm says what was kept instead of leaving a stale message
- T36: The setup checklist shows its final tick for a beat before hiding
- T37: "Add all results" with no results says there is nothing to add instead of "Added 0"
- T38: Scores and tooltips say "1 week ago", not "1 week(s) ago"
- T39: The Sources Remove column is sized to its button instead of taking most of the table
- T40: The installer only promises a desktop icon when one was actually placed
- T41: The detail view caps the file list at 5 with "and N more" instead of one endless line
- T42: Watch "new" flags survive quiet runs until the user has seen them
- T43: The watch dialog keeps its selection on refresh and shows "Resume this watch" for paused watches
- T44: Watch and Queue dialog buttons are disabled when there is nothing to act on
- T45: Failed queue entries can be retried from the dialog
- T46: An offline Check now says the app is offline instead of staying silent
- T47: Collapsible-section state is remembered across restarts
- T48: The Watch and Queue empty states teach how to fill them
- T49: The detail pane's link is clickable
- T50: The download success log is one line with one number and no full path
- T51: Check STELLA-ready with nothing selected checks the whole list, like link check
- T52: The shortcuts overlay teaches Enter, Ctrl+A, and double-click
- T53: A .bak recovery is recorded in About so it survives the next status update
- T54: The Sources empty-state guidance sits above the table, where the eye lands first
- T55: Search results have a "Find in these results" filter box, like My List
- T56: A converted row has actions to open its converted file or folder
- T57: My List has a Score column, kept in step with the result it came from
- T58: The Edit dialog lets you fix a mistyped URL, with the same inline check as adding
- T59: Edits can be undone, like removes
- T60: "Add a link I found" is in the File menu and on My List, not only on the Search tab
- T61: A download history records what was converted, when, and how many rows
- T62: "Remove the dead ones" tidies all dead rows after a link check
- T63: Links can move between collections
- T64: The Help menu runs the self-test in the app and shows the plain-words result
- T65: The Active-only 12-month default stays as it is; the user's own window choice now persists across restarts (the default itself is still Joshua's call)

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
- Verification fix: the command line search now says how many results the filters hid, like the window does, instead of a bare "No results."

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
