# Filesystem Agent Example

Run a ReAct agent that uses DiskFileSystem tools to browse `/userinput/`, read relevant files, and write a summary to `/workspace/summary.md`.

## Environment

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional; default: `gpt-4o-mini`)
- `OPENAI_BASE_URL` (optional; default: `https://api.openai.com/v1`)
 - `.env` in the working directory is auto-loaded via `python-dotenv`.

Install extra dependency for the example:

```bash
pip install python-dotenv
```

## Run

```bash
OPENAI_API_KEY=sk-xxx OPENAI_MODEL=gpt-4o-mini \
python examples/filesystem_agent/main.py --topic "your topic"
```

Place demo data under the physical directory mapped to `/userinput/` (created when DiskFileSystem initializes). The agent will write results to `/workspace/summary.md` and list `/workspace/`.
