# Permission-aware image analysis

This example gives an AgentScope agent two small, measurable image tools:

- `inspect_image` reads a named image (or rectangular region) and reports its
  grayscale minimum, maximum, mean, and pixel count. It does not write a file.
- `segment_otsu` computes a binary mask using Otsu's threshold and saves it as
  a PNG. AgentScope asks the user to approve this write before the tool runs.

The agent can describe **measurements**, but the mask is not a validated defect
detector. White pixels mean the bright side of the selected grayscale region.

## Run without an API key

From the repository root, install AgentScope and this example's image
dependency:

```bash
python -m pip install -e .
python -m pip install -r examples/image_analysis/requirements.txt
python -m examples.image_analysis.main demo --decision approve
python -m examples.image_analysis.main demo --decision deny
```

The demo creates a 32 × 32 test image. Its dark background is 30 and its
central bright square is 200. The read tool reports a mean of 72.5; Otsu's
threshold is 30 and the bright foreground occupies 25% of the image. Each run
has a separate directory under `examples/image_analysis/.demo-output/`. The
JSON report includes the decision, tool results, and output paths. Before the
decision there are no output PNGs. Approval creates one PNG; denial creates
none. A third call shows how a missing image becomes a tool error.

To keep output elsewhere, pass `--work-dir PATH`.

## Try a model-backed console

Put 8-bit images in a directory you control, then run:

```bash
export OPENAI_API_KEY=your-key
python -m examples.image_analysis.main chat --model YOUR_MODEL \
  --input-dir PATH_TO_IMAGES --output-dir PATH_TO_RESULTS
```

On PowerShell, set the environment variable with
`$env:OPENAI_API_KEY = "your-key"`. `OPENAI_BASE_URL` is optional for an
OpenAI-compatible endpoint. The console handles approval requests from
AgentScope. This mode calls a model provider and requires valid credentials;
the offline demo above exercises the same image tools without a provider.

Tools accept filenames only inside the configured input directory, not paths
to arbitrary files. Images are limited to one frame and 20 million pixels.
16-bit and floating-point images must be converted before use. EXIF rotation
is applied before region coordinates; transparent pixels are composited on
white. The output PNG covers the selected region rather than the full source
image, and the source is never modified.
