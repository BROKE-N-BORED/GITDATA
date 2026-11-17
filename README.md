# Dataset Scout

Find training datasets for STELLA, build lists of links. That is the
whole job.

Dataset Scout searches GitHub repos and Hugging Face datasets live,
lets you add any other website to search on, and keeps a saved list
of links you can check, copy, and export, including a STELLA
manifest for a STELLA project.

## Install

Unzip a release anywhere, then double-click **Install Dataset Scout**
in the folder (or run one short command in a terminal inside the
folder):

`bash install.sh`

If your file manager asks whether to trust the file, choose to
allow launching it.

That sets up Dataset Scout's own Python environment, puts **Dataset
Scout** in the app menu with its magnifier icon, and puts a **Dataset
Scout** icon on the desktop. After that, just click it. No terminal
needed to run it.

The one-time download is about 200 MB: the interface toolkit plus
the Parquet reader (pyarrow, about 169 MB on disk once installed),
so Parquet datasets convert too.

To launch from a terminal instead:

`bash run.sh`

## How to use it

**Search tab**: type a few words, pick a category (All,
Post-training, Instruction tuning, Reasoning, Training datasets),
press Search. Results come from GitHub and Hugging Face at the
same time, newest activity first. "Active only" keeps only
datasets updated recently (3, 6, 12 or 24 months, or any age);
the stars filter keeps only repos with at least that many stars
(Hugging Face likes count the same way). The license filter keeps
only results with a chosen license, such as Apache 2.0 or MIT.
Click a column heading to sort. Pick rows and add them to
My List, or add all results at once. The search box remembers
your last 10 searches, and repeating a search within the hour
answers from the cache instead of asking the sites again.
"Check STELLA-ready" looks at a selected dataset's real files
and says whether STELLA can use them, needs conversion, or there
is nothing to convert.

**Search on other websites**: the buttons under the results open
the same search in your browser on Hugging Face and Kaggle. Found
something good there? "Add a link I found" opens
a form and it goes straight into My List.

**My List tab**: your saved links, in named collections: keep
several lists, switch between them, rename them, delete them.
Double-click a row to open it, or right-click for Open, Edit, Copy
link, Mark as, or Remove. Each link has a state: Found,
Downloaded, Converted, or In STELLA, following the real pipeline.
Drag rows to reorder them. "Check links" tests every link
(working, dead, or unknown). "Copy all links" copies the plain
URLs. Export as a Markdown list, plain JSON, or a STELLA
manifest; import a list back from any of those. "Send to STELLA"
writes the manifest into STELLA's home folder when STELLA is
installed on this PC. "Download and convert" downloads selected
datasets and converts their JSON, JSONL, CSV, and TSV files into
STELLA `.jsonl` in a `stella-ready` folder. Removing a link asks
first and can be undone.

**Sources tab**: add or remove websites to search on. A website
address can hold `{q}` where your search words go, or be a plain
address. "Restore default websites" brings back the two built-ins.
The optional GitHub token lives here too: stored on this PC only,
never in code, never in a log. Without one, GitHub allows fewer
searches per minute; the status bar shows how many searches are
left. "Copy debug report" copies the recent log with any token
removed, for getting help.

**Menus and shortcuts**: File holds export, import, Send to
STELLA, and exit; Edit holds undo, remove, and clear; View jumps
to a tab; Help holds How to Use and Check for updates. Ctrl+F
focuses the search box, Ctrl+E exports for STELLA, Delete removes
a selected link.

## STELLA format note

STELLA trains on a `.jsonl` file with one JSON object per line,
either `{"prompt", "completion"}` or `{"messages": [{"role",
"content"}, ...]}`, one format per file, at least 2 rows. Dataset
Scout's Download and convert writes exactly that shape into the
`stella-ready` folder, and those `.jsonl` files are what STELLA
trains on. Send to STELLA only sends the link-list manifest;
STELLA reads datasets by file path. Parquet files convert when
the Parquet reader is installed; Arrow files are not converted:
Scout names them as unsupported instead of
pretending. Rows that cannot become a prompt and completion pair,
such as text with no prompt to pair it with, are skipped, not
faked.

## Command line

After install, `scout` works in a terminal:

`scout search "reasoning dataset" --category reasoning`

`scout sources list`

`scout list`

`scout add https://github.com/example/data --title "Example data"`

`scout remove https://github.com/example/data`

`scout check`

`scout import my-list.md`

`scout export stella stella-manifest.json`

`scout update-check`

`scout --self-test`

Before install, the same commands run as `bash scout-cli ...` from
inside the Dataset Scout folder.

## Where your data lives

All Dataset Scout data lives in `~/.dataset-scout/` as plain JSON:
your saved list (`mylist.json`), your websites (`sources.json`),
and your settings (`config.json`, including the GitHub token).
Logs live under `~/.dataset-scout/logs/`. Set `SCOUT_HOME` to move it.

## Tests

`bash run_tests.sh`. The suite uses fake GitHub and Hugging Face
replies, so it never touches the live network.

## License

MIT. See [LICENSE](LICENSE).
