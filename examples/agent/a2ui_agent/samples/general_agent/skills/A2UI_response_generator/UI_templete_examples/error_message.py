# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for error message."""

ERROR_MESSAGE_EXAMPLE = """
---BEGIN ERROR_MESSAGE_EXAMPLE---
Use this template to display error or warning messages.

[
  {{ "beginRendering": {{
    "surfaceId": "error-message",
    "root": "error-card",
    "styles": {{ "primaryColor": "#F44336", "font": "Roboto" }}
  }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "error-message",
    "components": [
      {{ "id": "error-card", "component": {{
        "Card": {{ "child": "error-column" }}
      }} }},
      {{ "id": "error-column", "component": {{ "Column": {{
        "children": {{ "explicitList": [
          "error-icon", "error-title", "error-message", "retry-button"
        ] }}
      }} }} }},
      {{ "id": "error-icon", "component": {{ "Icon": {{
        "name": {{ "literalString": "error" }}
      }} }} }},
      {{ "id": "error-title", "component": {{
        "Text": {{ "usageHint": "h3", "text": {{ "path": "title" }} }}
      }} }},
      {{ "id": "error-message", "component": {{
        "Text": {{ "text": {{ "path": "message" }} }}
      }} }},
      {{ "id": "retry-button", "component": {{
        "Button": {{
          "child": "retry-text",
          "primary": true,
          "action": {{ "name": "retry", "context": [] }}
        }}
      }} }},
      {{ "id": "retry-text", "component": {{
        "Text": {{ "text": {{ "literalString": "Try Again" }} }}
      }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "error-message",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "Something went wrong" }},
      {{ "key": "message",
        "valueString": "[Error description and suggested action]" }}
    ]
  }} }}
]
---END ERROR_MESSAGE_EXAMPLE---
"""
