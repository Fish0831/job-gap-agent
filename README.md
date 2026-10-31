# job-gap-agent

Paste a job posting and get a gap report: which required skills I can already prove with my own projects, which are only partial, which are missing, and what to learn next.

**Status:** work in progress — v1 (command-line gap report) targeted for 2026-11-08. See the [design doc](docs/design.md) (in Traditional Chinese).

## How it works (v1)

1. **Extract** — an LLM turns a job posting (Chinese or English) into structured requirements, validated with Pydantic.
2. **Match** — each requirement is compared with my skill profile (`profile.yaml`). A skill only counts as "have" if it points to a real project as evidence; evidence IDs are checked in code so the model cannot invent experience.
3. **Report** — a Markdown gap report with the top skills to work on next.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows (Git Bash / MSYS2); use .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
cp profile.example.yaml profile.yaml   # then fill in your own projects and skills
```

## Tests

```bash
python -m pytest -v
```

## Privacy

`jobs/` (raw job postings) and `profile.yaml` (personal skill profile) are git-ignored. `profile.example.yaml` shows the format.
