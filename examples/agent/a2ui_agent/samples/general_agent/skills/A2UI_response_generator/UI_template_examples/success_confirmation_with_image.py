# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for success confirmation with image."""
# Confirmation examples
SUCCESS_CONFIRMATION_WITH_IMAGE_EXAMPLE = """
---BEGIN SUCCESS_CONFIRMATION_WITH_IMAGE_EXAMPLE---
Use this template to display success confirmations after an action.

[
  {{ "beginRendering": {{ "surfaceId": "confirmation", "root": "confirmation-card", "styles": {{ "primaryColor": "#4CAF50", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "confirmation",
    "components": [
      {{ "id": "confirmation-card", "component": {{ "Card": {{ "child": "confirmation-column" }} }} }},
      {{ "id": "confirmation-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["success-icon", "confirm-title", "confirm-image", "divider1", "confirm-details", "divider2", "confirm-message", "action-button"] }} }} }} }},
      {{ "id": "success-icon", "component": {{ "Icon": {{
        "name": {{ "literalString": "check" }}
      }} }} }},
      {{ "id": "confirm-title", "component": {{ "Text": {{
        "usageHint": "h2", "text": {{ "path": "title" }}
      }} }} }},
      {{ "id": "confirm-image", "component": {{ "Image": {{
        "url": {{ "path": "imageUrl" }}, "usageHint": "mediumFeature"
      }} }} }},
      {{ "id": "confirm-details", "component": {{ "Text": {{
        "text": {{ "path": "details" }}
      }} }} }},
      {{ "id": "confirm-message", "component": {{ "Text": {{
        "usageHint": "h5", "text": {{ "path": "message" }}
      }} }} }},
      {{ "id": "divider1", "component": {{ "Divider": {{}} }} }},
      {{ "id": "divider2", "component": {{ "Divider": {{}} }} }},
      {{ "id": "action-button", "component": {{ "Button": {{
        "child": "action-text", "action": {{ "name": "dismiss", "context": [] }}
      }} }} }},
      {{ "id": "action-text", "component": {{ "Text": {{
        "text": {{ "literalString": "Done" }}
      }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "confirmation",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "[Confirmation Title]" }},
      {{ "key": "details", "valueString": "[Booking/Action Details]" }},
      {{ "key": "message", "valueString": "We look forward to seeing you!" }},
      {{ "key": "imageUrl", "valueString": "[Image URL]" }}
    ]
  }} }}
]
---END SUCCESS_CONFIRMATION_WITH_IMAGE_EXAMPLE---
"""
