---
name: A2UI_response_generator
description: A skill that can retrieve A2UI UI JSON schematics and UI templates that best show the response. This skill is essential and must be used before generating A2UI (Agent to UI) JSON responses.
---

# A2UI response generation Skill

## Overview

This skill is **essential and must be used before generating A2UI (Agent to UI) JSON responses**. It enables agents to retrieve A2UI UI JSON schematics and UI templates that best show the response, allowing for the generation of rich, interactive UI responses using the A2UI protocol.

Instead of loading the entire A2UI schema and all examples at once, this skill allows agents to **retrieve** only the relevant UI templates and schematics based on the response content. The A2UI protocol defines a JSON-based format for dynamically constructing and updating user interfaces. By breaking down the examples into modular templates, agents can:

1. Retrieve the appropriate A2UI UI JSON schematics for validation and structure reference
2. Select UI templates that best match and display the response content
3. Reduce prompt token usage by loading only necessary templates
4. Easily extend with new UI templates for different domains

## Quick Start

When it is required to generate UI JSON, follow these steps:

### Step 1: Load the A2UI Schema

Run the following script to load the complete A2UI schema. The skill directory path is provided in the skill description above (look for the directory path in parentheses after the skill name).

**Use the `execute_shell_command` tool to run:**
```bash
python view_a2ui_schema.py
```

About detailed usage, please refer to the `./view_a2ui_schema.py` script (located in the same folder as this SKILL.md file).

### Step 2: Select UI Template Examples

Select appropriate UI template examples based on your response content. The skill directory path is provided in the skill description above (look for the directory path in parentheses after the skill name).

**IMPORTANT**: You MUST use the **exact template names** listed in the "Available templates" table below. Do NOT use generic category names like 'list', 'form', 'confirmation', or 'detail'. You MUST use the specific template name (e.g., `SINGLE_COLUMN_LIST`, `BOOKING_FORM`, etc.).

**Use the `execute_shell_command` tool to run:**
```bash
python view_a2ui_examples.py --template_names SINGLE_COLUMN_LIST BOOKING_FORM
```

**Available templates** (you MUST use these exact names, case-sensitive):

| Template Name | Use Case |
| --- | --- |
| `SINGLE_COLUMN_LIST` | Vertical list with detailed cards (for ≤5 items) |
| `TWO_COLUMN_LIST` | Grid layout with cards (for >5 items) |
| `SIMPLE_LIST` | Compact list without images |
| `BOOKING_FORM` | Reservation, booking, registration |
| `SEARCH_FILTER_FORM` | Search forms with filters |
| `CONTACT_FORM` | Contact or feedback forms |
| `SUCCESS_CONFIRMATION` | Success message after action |
| `ERROR_MESSAGE` | Error or warning display |
| `INFO_MESSAGE` | Informational messages |
| `ITEM_DETAIL_CARD` | Detailed view of single item |
| `PROFILE_VIEW` | User or entity profile display |

**Template Selection Guide:**
- For **list display** with ≤5 items: Use `SINGLE_COLUMN_LIST`
- For **list display** with >5 items: Use `TWO_COLUMN_LIST`
- For **compact lists** without images: Use `SIMPLE_LIST`
- For **booking/reservation forms**: Use `BOOKING_FORM`
- For **search forms with filters**: Use `SEARCH_FILTER_FORM`
- For **contact/feedback forms**: Use `CONTACT_FORM`
- For **success confirmations**: Use `SUCCESS_CONFIRMATION`
- For **error messages**: Use `ERROR_MESSAGE`
- For **info messages**: Use `INFO_MESSAGE`
- For **item detail views**: Use `ITEM_DETAIL_CARD`
- For **profile views**: Use `PROFILE_VIEW`

**Remember**: Always use the exact template names from the table above. Never use generic terms like 'list' or 'form' - they are NOT valid template names.

About detailed usage, please refer to the `./view_a2ui_examples.py` script (located in the same folder as this SKILL.md file).

### Step 3: Generate the A2UI Response

Once you have loaded the schema and examples, construct your A2UI response following these rules:

1. Your response MUST be in two parts, separated by the delimiter: `---a2ui_JSON---`
2. The first part is your conversational text response
3. The second part is a single, raw JSON object which is a list of A2UI messages
4. The JSON part MUST validate against the A2UI schema

## File Structure

```
A2UI_response_generator/
├── SKILL.md                          # This file - main skill documentation
├── view_a2ui_schema.py               # Tool to view the complete A2UI schema (schema included in file)
├── view_a2ui_examples.py             # Tool to view UI template examples (templates included in file)
└── __init__.py                       # Package initialization
```

## Domain-Specific Extensions

To add support for a new domain (e.g., flight booking, e-commerce), add new templates to `view_a2ui_examples.py`:

1. Define a new template constant in `view_a2ui_examples.py` (e.g., `FLIGHT_BOOKING_FORM_EXAMPLE`)
2. Add the template to the `TEMPLATE_MAP` dictionary in `view_a2ui_examples.py`
3. Update this SKILL.md to include the new templates in the available templates list

## Response Format

The final A2UI response should follow this format:

```
[Your conversational response here]

---a2ui_JSON---
[
  { "beginRendering": { ... } },
  { "surfaceUpdate": { ... } },
  { "dataModelUpdate": { ... } }
]
```

## Troubleshooting

If you encounter any issues running the scripts, make sure:

1. You are in the correct skill directory (check the skill description for the actual path)
2. The script files (`view_a2ui_schema.py` and `view_a2ui_examples.py`) exist in the skill directory
3. You have the required Python dependencies installed

For detailed usage of each script, please refer to:
- `./view_a2ui_schema.py` - View the A2UI schema
- `./view_a2ui_examples.py` - View A2UI template examples
