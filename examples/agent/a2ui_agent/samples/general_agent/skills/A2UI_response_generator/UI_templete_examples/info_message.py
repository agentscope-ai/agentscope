# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for info message."""

INFO_MESSAGE_EXAMPLE = """
---BEGIN INFO_MESSAGE_EXAMPLE---
Use this template to display informational messages.

[
  {{ "beginRendering": {{
    "surfaceId": "info-message",
    "root": "info-card",
    "styles": {{ "primaryColor": "#2196F3", "font": "Roboto" }}
  }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "info-message",
    "components": [
      {{ "id": "info-card", "component": {{ "Card": {{ "child": "info-column" }} }} }},
      {{ "id": "info-column", "component": {{
        "Column": {{
          "children": {{
            "explicitList": [
              "info-icon", "info-title", "info-message", "dismiss-button"
            ]
          }}
        }}
      }} }},
      {{ "id": "info-icon", "component": {{ "Icon": {{ "name": {{ "literalString": "info" }} }} }} }},
      {{ "id": "info-title", "component": {{
        "Text": {{ "usageHint": "h3", "text": {{ "path": "title" }} }}
      }} }},
      {{ "id": "info-message", "component": {{
        "Text": {{ "text": {{ "path": "message" }} }}
      }} }},
      {{ "id": "dismiss-button", "component": {{
        "Button": {{
          "child": "dismiss-text",
          "action": {{ "name": "dismiss", "context": [] }}
        }}
      }} }},
      {{ "id": "dismiss-text", "component": {{
        "Text": {{ "text": {{ "literalString": "Got it" }} }}
      }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "info-message",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "[Info Title]" }},
      {{ "key": "message",
        "valueString": "[Informational message]" }}
    ]
  }} }}
]
---END INFO_MESSAGE_EXAMPLE---
"""
