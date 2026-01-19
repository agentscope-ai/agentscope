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

Important: Please use the `execute_shell_command` tool to execute Python command.

### Step 1: Load the A2UI Schema

Run the following script to load the complete A2UI schema.

Currently available `schema_category` is `BASE_SCHEME`.

**Use the `execute_shell_command` tool to run (make sure you are in the skill directory):**
```bash
python -m view_a2ui_schema --schema_category BASE_SCHEME
```

**Usage**: `python -m view_a2ui_schema --schema_category [schema_category]` - Loads the A2UI schema definition for validating A2UI JSON response structure. Currently only `BASE_SCHEME` is available.

About detailed usage, please refer to the `./view_a2ui_schema.py` script (located in the same folder as this SKILL.md file).

### Step 2: Select UI Template Examples

Select appropriate UI template examples based on your response content.

**IMPORTANT**: You MUST use the **exact template names** listed in the "Available UI template examples" table below. Do NOT use generic category names like 'list', 'form', 'confirmation', or 'detail'. You MUST use the specific template name (e.g., `SINGLE_COLUMN_LIST_WITH_IMAGE`, `BOOKING_FORM_WITH_IMAGE`, etc.).

**Use the `execute_shell_command` tool to run (make sure you are in the skill directory):**
```bash
python -m view_a2ui_examples --template_name SINGLE_COLUMN_LIST_WITH_IMAGE
```

**Usage**: `python -m view_a2ui_examples --template_name [template_name]` - Loads a UI template example for reference when generating A2UI responses. Accepts a single template name from the available templates table below.

**Available UI template examples** (when `schema_category` is `BASE_SCHEME`, you MUST use these exact names, case-sensitive):

| Template Name | Use Case | Selection Guide | Image Support |
| --- | --- | --- | --- |
| `SINGLE_COLUMN_LIST_WITH_IMAGE` | Vertical list with detailed cards (for ≤5 items) | Use for **list display** with ≤5 items | ✅ With image |
| `TWO_COLUMN_LIST_WITH_IMAGE` | Grid layout with cards (for >5 items) | Use for **list display** with >5 items | ✅ With image |
| `SIMPLE_LIST` | Compact list without images | Use for **compact lists** without images | ❌ Without image |
| `SELECTION_CARD` | Multiple choice questions | Use for **multiple choice questions** | ❌ Without image |
| `MULTIPLE_SELECTION_CARDS` | Multiple selection cards in a list | Use for **multiple selection cards** displayed together | ❌ Without image |
| `BOOKING_FORM_WITH_IMAGE` | Reservation, booking, registration | Use for **booking/reservation forms** | ✅ With image |
| `SEARCH_FILTER_FORM_WITH_IMAGE` | Search forms with filters | Use for **search forms with filters** | ❌ Without image |
| `CONTACT_FORM_WITH_IMAGE` | Contact or feedback forms | Use for **contact/feedback forms** | ❌ Without image |
| `EMAIL_COMPOSE_FORM_WITH_IMAGE` | Email composition forms | Use for **email composition forms** | ❌ Without image |
| `SUCCESS_CONFIRMATION_WITH_IMAGE` | Success message after action | Use for **success confirmations** | ✅ With image |
| `ERROR_MESSAGE` | Error or warning display | Use for **error messages** | ❌ Without image |
| `INFO_MESSAGE` | Informational messages | Use for **info messages** | ❌ Without image |
| `ITEM_DETAIL_CARD` | Detailed view of single item | Use for **item detail views** | ✅ With image |
| `ITEM_DETAIL_CARD_WITH_IMAGE` | Detailed view of single item with image | Use for **item detail views** with images | ✅ With image |
| `PROFILE_VIEW` | User or entity profile display | Use for **profile views** | ✅ With image |

**Remember**: Always use the exact template names from the table above. Never use generic terms like 'list' or 'form' - they are NOT valid template names.

About detailed usage, please refer to the `./view_a2ui_examples.py` script (located in the same folder as this SKILL.md file).

### Step 3: Generate the A2UI Response

After loading the schema and examples, construct your A2UI response using the **generate_response** tool. The `response_with_a2ui` parameter must be a string containing two parts separated by the delimiter `---a2ui_JSON---`:

First Part: **Conversational text response**: Your natural language reply to the user
Second Part. **A2UI JSON messages**: A raw JSON array of A2UI message objects that MUST validate against the A2UI schema

**Format:**
```
[Your conversational response here]

---a2ui_JSON---
[
  { "beginRendering": { ... } },
  { "surfaceUpdate": { ... } },
  { "dataModelUpdate": { ... } }
]
```

**Important**: The JSON portion must be valid JSON and conform to the A2UI schema loaded in Step 1.

## File Structure

```
A2UI_response_generator/
├── SKILL.md                          # This file - main skill documentation
├── view_a2ui_schema.py               # Tool to view the complete A2UI schema (schema included in file)
├── view_a2ui_examples.py             # Tool to view UI template examples (templates included in file)
├── __init__.py                       # Package initialization
├── schema/                           # A2UI schema definitions
│   ├── __init__.py
│   └── base_schema.py                # Base A2UI schema
└── UI_templete_examples/             # UI template examples
    ├── __init__.py
    ├── booking_form.py               # Booking form template
    ├── contact_form.py               # Contact form template
    ├── email_compose_form.py         # Email compose form template
    ├── error_message.py              # Error message template
    ├── info_message.py               # Info message template
    ├── item_detail_card_with_image.py # Item detail card with image template
    ├── profile_view.py               # Profile view template
    ├── search_filter_form.py         # Search filter form template
    ├── simple_column_list_without_image.py # Simple list template
    ├── single_column_list.py         # Single column list template
    ├── success_confirmation_with_image.py # Success confirmation template
    └── two_column_list.py            # Two column list template
```

## Domain-Specific Extensions

To add support for a new domain (e.g., flight booking, e-commerce), add new templates to `view_a2ui_examples.py`:

1. Define a new template constant in `view_a2ui_examples.py` (e.g., `FLIGHT_BOOKING_FORM_EXAMPLE`)
2. Add the template to the `TEMPLATE_MAP` dictionary in `view_a2ui_examples.py`
3. Update this SKILL.md to include the new templates in the available templates list


## Troubleshooting

If you encounter any issues running the scripts, make sure:
you use the tool `execute_shell_command` run the python command.

1. You are in the correct skill directory (check the skill description for the actual path)
2. The script files (`view_a2ui_schema.py` and `view_a2ui_examples.py`) exist in the skill directory
3. You have the required Python dependencies installed

For detailed usage of each script, please refer to:
- `./view_a2ui_schema.py` - View the A2UI schema
- `./view_a2ui_examples.py` - View A2UI template examples
