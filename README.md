# Dataset Scout

Find training datasets for STELLA, build lists of links. That is the
whole job.

Dataset Scout searches GitHub repos and Hugging Face datasets live,
lets you add any other website to search on, and keeps a saved list
of links you can check, copy, and export — including a STELLA
manifest for a STELLA project.

## Install

Unzip a release anywhere, then double-click **Install Dataset Scout**
in the folder (or run one short command in a terminal inside the
folder):

`bash install.sh`

That sets up Dataset Scout's own Python environment, puts **Dataset
Scout** in the app menu with its magnifier icon, and puts a **Dataset
Scout** icon on the desktop. After that, just click it — no terminal
needed to run it.

To launch from a terminal instead:

`bash run.sh`

## How to use it

**Search tab** — type a few words, pick a category (All,
Post-training, Instruction tuning, Reasoning, Training datasets),
press Search. Results come from GitHub and Hugging Face, newest
activity first. "Active only" keeps only datasets updated recently
(3, 6, 12 or 24 months, or any age); the stars filter keeps only
repos with at least that many stars. Pick rows and add them to
My List, or add all results at once.

**Search on other websites** — the buttons under the results open
the same search in your browser on Hugging Face, Kaggle, and Papers
With Code. Found something good there? Paste it into "Add a link I
found" and it goes straight into My List.

**My List tab** — your saved links. Double-click a row to open it.
"Check links" tests every link (working, dead, or unknown).
"Copy all links" copies the plain URLs. Export as a Markdown list,
plain JSON, or a STELLA manifest.

**Sources tab** — add or remove websites to search on. A website
address can hold `{q}` where your search words go, or be a plain
address. "Restore default websites" brings back the three built-ins.
The optional GitHub token lives here too: stored on this PC only,
never in code, never in a log. Without one, GitHub allows fewer
searches per hour. "Copy debug report" copies the recent log with
any token removed, for getting help.

## STELLA format note

Before STELLA can use a dataset from one of these links, the
downloaded data must be converted to STELLA's format: a `.jsonl`
file with one JSON object per line, either `{"prompt",
"completion"}` or `{"messages": [{"role", "content"}, ...]}`, one
format per file, at least 2 rows. Dataset Scout only finds links —
it does not download or convert the data.

## Command line

After install, `scout` works in a terminal:

`scout search "reasoning dataset" --category reasoning`

`scout sources list`

`scout list`

`scout export stella stella-manifest.json`

Before install, the same commands run as `bash scout-cli ...` from
inside the Dataset Scout folder.

## Where your data lives

All Dataset Scout data lives in `~/.dataset-scout/` as plain JSON:
your saved list (`mylist.json`), your websites (`sources.json`),
and your settings (`config.json`, including the GitHub token).
Logs live under `~/.dataset-scout/logs/`. Set `SCOUT_HOME` to move it.

## Tests

`bash run_tests.sh` — the suite uses fake GitHub and Hugging Face
replies, so it never touches the live network.

## License

MIT — see [LICENSE](LICENSE).
