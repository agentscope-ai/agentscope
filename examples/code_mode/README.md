# Python Code Mode

This example uses DashScope to generate and execute Python through
one `run_code` tool. Python calls native tools and returns selected results to
the model.

## Run

From the repository root:

```sh
conda activate agentscope
pip install -e '.[code-mode]'
export DASHSCOPE_API_KEY="your-api-key"
python examples/code_mode/main.py
```

The model makes real API requests. The sales tool uses sample data; the expected
result is **4 records totaling 100**. Response wording may vary.

## How it works

Pass a `CodeModeToolkit` wrapping your native `Toolkit` to an `Agent`.
See [main.py](main.py) for the complete setup.

Generated Python discovers tools with `ALL_TOOLS` or `describe_tool()`, calls
`await tools.<alias>(**kwargs)`, and selects output with `text()`, `print()`,
`image()`, `audio()`, or `video()`. Intermediate results stay in Python unless
selected for output.

## Limits

Each call starts a fresh Python process. Execution and output limits apply;
variables do not persist between calls.

LocalBackend and BubblewrapBackend are supported. Local execution has host-user
permissions and is **not a security sandbox**. Nested tools retain permission
checks, but calls requiring confirmation or external execution return errors.
Completed side effects are not rolled back.
