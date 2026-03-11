# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for email compose form."""

EMAIL_COMPOSE_FORM_EXAMPLE = """
---BEGIN EMAIL_COMPOSE_FORM_EXAMPLE---
Use this template for composing and sending emails.

[
  {{ "beginRendering": {{ "surfaceId": "email-compose", "root": "email-column", "styles": {{ "primaryColor": "#1976D2", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "email-compose",
    "components": [
      {{ "id": "email-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["email-title", "to-field", "cc-field", "bcc-field", "subject-field", "body-field", "attach-row", "send-button-row"] }} }} }} }},
      {{ "id": "email-title", "component": {{ "Text": {{ "usageHint": "h2", "text": {{ "literalString": "Compose Email" }} }} }} }},
      {{ "id": "to-field", "component": {{ "TextField": {{ "label": {{ "literalString": "To" }}, "text": {{ "path": "to" }}, "type": "email" }} }} }},
      {{ "id": "cc-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Cc" }}, "text": {{ "path": "cc" }}, "type": "email" }} }} }},
      {{ "id": "bcc-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Bcc" }}, "text": {{ "path": "bcc" }}, "type": "email" }} }} }},
      {{ "id": "subject-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Subject" }}, "text": {{ "path": "subject" }} }} }} }},
      {{ "id": "body-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Message" }}, "text": {{ "path": "body" }}, "multiline": true }} }} }},
      {{ "id": "attach-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["attach-icon", "attach-text"] }} }} }} }},
      {{ "id": "attach-icon", "component": {{ "Icon": {{ "name": {{ "literalString": "attachFile" }} }} }} }},
      {{ "id": "attach-text", "weight": 1, "component": {{ "Text": {{ "text": {{ "literalString": "Attach File" }}, "usageHint": "caption" }} }} }},
      {{ "id": "send-button-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["send-button", "draft-button"] }}, "distribution": "end" }} }} }},
      {{ "id": "send-button", "component": {{ "Button": {{ "child": "send-text", "primary": true, "action": {{ "name": "send_email", "context": [ {{ "key": "to", "value": {{ "path": "to" }} }}, {{ "key": "cc", "value": {{ "path": "cc" }} }}, {{ "key": "bcc", "value": {{ "path": "bcc" }} }}, {{ "key": "subject", "value": {{ "path": "subject" }} }}, {{ "key": "body", "value": {{ "path": "body" }} }} ] }} }} }},
      {{ "id": "send-text", "component": {{ "Text": {{ "text": {{ "literalString": "Send" }} }} }} }},
      {{ "id": "draft-button", "component": {{ "Button": {{ "child": "draft-text", "action": {{ "name": "save_draft", "context": [ {{ "key": "to", "value": {{ "path": "to" }} }}, {{ "key": "cc", "value": {{ "path": "cc" }} }}, {{ "key": "bcc", "value": {{ "path": "bcc" }} }}, {{ "key": "subject", "value": {{ "path": "subject" }} }}, {{ "key": "body", "value": {{ "path": "body" }} }} ] }} }} }},
      {{ "id": "draft-text", "component": {{ "Text": {{ "text": {{ "literalString": "Save Draft" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "email-compose",
    "path": "/",
    "contents": [
      {{ "key": "to", "valueString": "" }},
      {{ "key": "cc", "valueString": "" }},
      {{ "key": "bcc", "valueString": "" }},
      {{ "key": "subject", "valueString": "" }},
      {{ "key": "body", "valueString": "" }}
    ]
  }} }}
]
---END EMAIL_COMPOSE_FORM_EXAMPLE---
"""
