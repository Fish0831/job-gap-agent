# job-gap-agent

**Live demo: https://job-gap-agent.streamlit.app** — upload a resume, get a market gap report against sample AI engineering job postings in Taiwan. No sign-up, no API key.

Paste job postings, get two reports:

- **Gap report (per job)** — which skills I can prove with real projects, which are only partial, which are missing, and what to work on first.
- **Market gap report (across jobs)** — stack many postings together and count which skills are required most often while I still lack them. Ranking is computed in code, not by the LLM.

Design doc (Traditional Chinese): [docs/design.md](docs/design.md)

## Web demo

```
upload resume ─► read file ─► mask email / phone ─► resume → skill profile draft (LLM)
                                                              │
samples/jobs.json (10 AI job postings, skill lists only) ─────┤
                                                              ▼
                          match against every posting in parallel ─► market gap report
```

- **Two tabs:** the market gap report (metric cards, a "fill these gaps first" chart, strongest skills, a sortable table) and the skill profile draft built from the resume (projects as evidence, skills with / without evidence, a YAML download).
- **Privacy:** the resume is never stored and run traces are switched off (`SAVE_TRAJECTORIES=0`). Email addresses and phone numbers are masked in code before anything is sent to Claude.
- **Public samples:** `samples/jobs.json` holds only job titles, companies and extracted skill lists — no posting text.
- **Cost control** (visitors use my API key): at most 3 analyses per browser session, a site-wide daily budget, and a monthly spend limit on a dedicated Anthropic Console workspace. One analysis costs about **US$0.01**. Cost is logged on the server, not shown on the page.
- `app.py` only draws the page. Parallel matching lives in `job_agent/pipeline.py` and is tested with a fake Claude client.

Run it locally:

```bash
python -m job_agent.samples jobs        # export samples/jobs.json from your own jobs/ (skill lists only)
python -m streamlit run app.py          # opens http://localhost:8501; reads ANTHROPIC_API_KEY from .env
```

To deploy on Streamlit Community Cloud, point it at `app.py` and put `ANTHROPIC_API_KEY` (and optionally `DAILY_BUDGET_USD`) in the app's Secrets — never in the repo.

## How it works

```
job posting ─► ① Extract (LLM, structured output, cached) ─► requirements
                                                                │
profile.yaml ───────────────────────────────────────────────────┤
                                                                ▼
             ③ Match: A clean up names (code) → B exact name match (code)
                      → C semantic mapping (LLM) → D decide status (code)
                                                                │
                      ④ Gap report per job (code) ◄─────────────┤
                      ⑤ Market gap report across jobs (code) ◄──┘
```

- **The LLM only does semantic judgement.** Name clean-up (synonyms table, spelling differences, soft-skill filter), status rules, ranking and reports are plain Python and covered by tests.
- **No evidence, no "have".** A skill only counts as "have" when it points to a project listed in `profile.yaml`. The LLM never outputs evidence; it is copied from the profile in code.
- **Structured outputs.** Pydantic model → JSON Schema in `output_config.format` → validated again with `model_validate_json()`.
- **Extraction cache.** Each extracted posting is cached under a fingerprint of model + extraction rules + posting text; change any of them and it is re-extracted automatically.

## First-time setup

Requires Python 3.10+ and an Anthropic API key.

```bash
git clone https://github.com/Fish0831/job-gap-agent.git
cd job-gap-agent
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash; PowerShell: .venv\Scripts\Activate.ps1; Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp profile.example.yaml profile.yaml # then fill in your own projects (evidence) and skills
```

Create a `.env` file in the project folder:

```
ANTHROPIC_API_KEY=sk-ant-...
```

## Every time

1. Save each job posting as a text file in `jobs/`, e.g. `jobs/11.txt`:

   ```
   title: AI Engineer
   company: ACME
   ---
   (paste the whole posting here)
   ```

2. Run:

   ```bash
   python -m job_agent jobs/11.txt          # one posting
   python -m job_agent jobs --market        # every posting in jobs/ + the market gap report
   ```

3. Open the reports in `reports/` (`<job>_<time>.md` and `market_<time>.md`).

Other options: `--profile my.yaml` (another skill profile), `--no-cache` (re-extract postings).

## Resume → profile draft

Don't want to write `profile.yaml` by hand? Turn a resume (`.pdf`, `.docx`, `.txt`, `.md`) into a draft:

```bash
python -m job_agent.resume resumes/my_resume.pdf                        # draft → reports/profile_draft_<time>.yaml
python -m job_agent.resume resumes/my_resume.pdf --compare profile.yaml # also compare with a hand-written profile
python -m job_agent jobs --market --profile reports/profile_draft_<time>.yaml
```

- Only concrete projects, jobs, research and awards become evidence. A skill that only appears in the skills section gets no evidence, so it shows up as "partial".
- The draft is conservative: `basic` by default, `working` at most. Review it before use.
- Code cleans up the LLM draft (soft skills, synonyms, duplicates, unknown evidence ids). Resume text is never saved to `trajectories/`, and `resumes/` is git-ignored.

Skill names that mean the same thing (e.g. `OpenAI API`, `Gemini API` → `LLM API Integration`) are unified with [`skill_synonyms.yaml`](skill_synonyms.yaml). Add a line there when you see duplicates in the market report.

## Models and cost

Both steps default to `claude-haiku-5-5`. To switch, add to `.env`:

```
EXTRACT_MODEL=claude-sonnet-5-5
MATCH_MODEL=claude-sonnet-5-5
```

Only models with a price in `PRICES` (`job_agent/llm.py`) are accepted, so the per-call cost limit ($0.20) always works.

Measured with Haiku on 10 real postings (2026-11): about **$0.0025 per posting** for the first run (extract + match), and about $0.001 per posting afterwards (extraction is cached). A 10-posting market report costs about $0.01–0.03.

Compare models on one posting (same cached extraction, only the matching model changes):

```bash
python -m job_agent.compare jobs/02.txt
```

On 3 postings, matching with Sonnet cost ~13× Haiku and was more lenient; the remaining differences were borderline skills, about the same size as re-running one model twice. So Haiku is the default.

## Tests

```bash
python -m pytest -q
```

61 tests. They use a fake Claude client (no network, no cost); the web page is tested with Streamlit's `AppTest`.

## Privacy

`jobs/`, `profile.yaml`, `resumes/`, `.env`, `.streamlit/secrets.toml`, `cache/`, `reports/` and `trajectories/` are git-ignored. `profile.example.yaml` shows the profile format.