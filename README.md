# Notebook Creator

A small local app for creating bug bounty research notebooks. Map functionalities, track test results, write session recaps, and keep a queue of next checks. Every target gets a portable HTML notebook and an adjacent JSON database, shared by your browsers.

**Python 3.10+ · No third-party runtime dependencies · MIT licensed**

## Start

```sh
python3 notebook_creator.py
```

Open **http://127.0.0.1:8765/**. Enter a target name, choose its folder name, and click **Create & open notebook**.

The app automatically creates:

```text
notebooks/
└── example/
    ├── notebook.html
    └── notebook.json
```

Keep the launcher running while you edit. Wait for **Saved to …json** before closing a notebook. Open the same launcher URL in another browser to continue with the same notes.

## What a notebook includes

- **Overview:** checked tests, open next checks, leads, recent sessions, and coverage by feature category.
- **Functionalities & tests:** editable feature maps and tags, checks you can add/edit/delete, per-check progress and results, notes, and JavaScript references. Editing a check keeps its recorded result; deleting it removes that result after confirmation.
- **Session recaps:** what you checked, what you learned, evidence, next steps, and scratch notes.
- **Next checks:** a prioritized queue with planned, in-progress, blocked, and done states.
- **Backups:** JSON import/export, a previous-save `.json.bak`, and atomic file writes.
- **Multiple browsers:** changes refresh from the shared file; stale revisions cannot overwrite newer notes.

New notebooks start empty. Record your observed behavior and hypotheses before marking a check reviewed or a lead confirmed.

## Command-line creation

```sh
python3 notebook_creator.py create example --target example.com
python3 notebook_creator.py create second-target --target second.example --program-url https://example.org/program
```

Existing folders are never overwritten. Use another name when you want a separate notebook.

Choose another storage folder or port:

```sh
python3 notebook_creator.py serve --root /path/to/research --port 8766
python3 notebook_creator.py create example --target example.com --root /path/to/research
```

Then use the URL printed by the launcher. Data lives in the chosen root; it does not depend on browser storage. The default root is `notebooks/` beside the Python script, even if you launch from another working directory.

## Copy, move, and recover notebooks

To move research to another computer, copy the target folder, including **both** `notebook.html` and `notebook.json`, into that installation’s notebook root. The creator lists notebooks in nested folders automatically. If you rename an HTML file, give its JSON file the same basename.

You can also copy `templates/notebook.html` into a target folder manually. On first open through the launcher, it creates a fresh adjacent JSON file and asks for the target name.

Use the launcher URL rather than double-clicking an HTML file. Browsers cannot silently save beside a `file://` page; direct-file mode is a temporary browser copy with JSON export for migration.

Each successful save keeps the previous JSON in `notebook.json.bak`. If a JSON file becomes unreadable, the launcher preserves it. Stop the launcher, recover it from a backup, and reopen. Save failures leave your working notes exportable on the page.

If two browsers edit the same notebook simultaneously, a stale save produces a conflict. Export the unsaved working copy before loading the latest JSON, then reapply the changes you need. The app never resolves a conflict by silently replacing newer data.

## Repository and publishing

The repository contains the app, blank template, and synthetic tests. Generated notebooks, JSON databases, backups, and local environment files are excluded by `.gitignore`.

Create an empty repository on GitHub, then publish this project:

```sh
git remote add origin https://github.com/YOUR-USERNAME/notebook-creator.git
git push -u origin main
```

Publishing the repository distributes the app. Each user runs the Python launcher locally and stores their own research files. The server binds to `127.0.0.1`; it has no remote-login or multi-user hosting system.

## Development

```sh
python3 -m unittest discover -s tests -v
node tests/test_client.cjs
```

Node.js 18+ is used only for testing the client save queue; it is not required to run the app. Tests exercise file persistence, notebook creation, conflicts, corruption handling, path boundaries, and edits made while a save or refresh is in flight. GitHub Actions runs them on each push and pull request.

```text
notebook_creator.py       Python launcher, creator, and JSON persistence
templates/notebook.html  Self-contained portable notebook UI
web/                     Notebook creation and library page
tests/                   Synthetic server and client tests
.github/workflows/       Automated checks
```

Notebook Creator stores local notes. It does not send requests to research targets or run security tests.
