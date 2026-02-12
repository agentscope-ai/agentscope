# Data Model: 009-l1-residual-fixes

## Entity: ToolChoiceMode

- **Fields**:
  - `value`: one of `auto`, `none`, `required`, or function name
  - `deprecated_alias`: `any` mapped to `required`
- **Validation rules**:
  - Input must be string
  - Unknown mode must exist in tool function names

## Entity: OmniAudioBlock

- **Fields**:
  - `type`: `input_audio`
  - `data`: URL or base64 string
- **Validation rules**:
  - URL data remains unchanged
  - Base64 data for omni models must include required prefix

## Relationship

- `ToolChoiceMode` affects model request kwargs generation.
- `OmniAudioBlock` transformation occurs before request dispatch.
