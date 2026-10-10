# OpenSandbox workspace

Run [main.py](main.py) to use an agent, pause its FastSandbox workspace, and
resume the same sandbox. See [configuration and usage](../../../docs/opensandbox_fastsandbox.md#user-demo).

- [build_template.py](build_template.py): build, publish, and validate a template
  in one command; use `--local` to verify the image with Docker only.
- [template/](template/): build a prepared gateway image for FastSandbox templates.
- [verify_pause_resume.py](verify_pause_resume.py): verify agent tools, filesystem,
  memory, and process persistence; delete the test sandbox afterward.
