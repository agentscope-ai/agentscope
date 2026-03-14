# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for contact form."""

CONTACT_FORM_EXAMPLE = """
---BEGIN CONTACT_FORM_EXAMPLE---
Use this template for contact or feedback forms.

[
  {{ "beginRendering": {{ "surfaceId": "contact-form", "root": "contact-column", "styles": {{ "primaryColor": "#4CAF50", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "contact-form",
    "components": [
      {{ "id": "contact-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["contact-title", "name-field", "email-field", "subject-field", "message-field", "send-button"] }} }} }} }},
      {{ "id": "contact-title", "component": {{ "Text": {{ "usageHint": "h2", "text": {{ "literalString": "Contact Us" }} }} }} }},
      {{ "id": "name-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Your Name" }}, "text": {{ "path": "name" }} }} }} }},
      {{ "id": "email-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Email Address" }}, "text": {{ "path": "email" }}, "type": "email" }} }} }},
      {{ "id": "subject-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Subject" }}, "text": {{ "path": "subject" }} }} }} }},
      {{ "id": "message-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Message" }}, "text": {{ "path": "message" }}, "multiline": true }} }} }},
      {{ "id": "send-button", "component": {{ "Button": {{ "child": "send-text", "primary": true, "action": {{ "name": "send_message", "context": [ {{ "key": "name", "value": {{ "path": "name" }} }}, {{ "key": "email", "value": {{ "path": "email" }} }}, {{ "key": "subject", "value": {{ "path": "subject" }} }}, {{ "key": "message", "value": {{ "path": "message" }} }} ] }} }} }} }},
      {{ "id": "send-text", "component": {{ "Text": {{ "text": {{ "literalString": "Send Message" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "contact-form",
    "path": "/",
    "contents": [
      {{ "key": "name", "valueString": "" }},
      {{ "key": "email", "valueString": "" }},
      {{ "key": "subject", "valueString": "" }},
      {{ "key": "message", "valueString": "" }}
    ]
  }} }}
]
---END CONTACT_FORM_EXAMPLE---
"""
