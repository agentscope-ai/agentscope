# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for simple column list without images."""

SIMPLE_LIST_EXAMPLE = """
---BEGIN SIMPLE_LIST_EXAMPLE---
Use this template for compact lists without images.

[
  {{ "beginRendering": {{ "surfaceId": "default", "root": "root-column", "styles": {{ "primaryColor": "#2196F3", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "default",
    "components": [
      {{ "id": "root-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["title-heading", "item-list"] }} }} }} }},
      {{ "id": "title-heading", "component": {{ "Text": {{ "usageHint": "h1", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "item-list", "component": {{ "List": {{ "direction": "vertical", "children": {{ "template": {{ "componentId": "list-item-template", "dataBinding": "/items" }} }} }} }} }},

      # 将 Row 改为 Button，使整行可点击
      {{ "id": "list-item-template", "component": {{ "Button": {{
        "usageHint": "listItem",
        "action": {{ "path": "action" }},
        "children": {{ "explicitList": ["item-icon", "item-content", "item-action"] }}
      }} }} }},

      {{ "id": "item-icon", "component": {{ "Icon": {{ "name": {{ "path": "icon" }} }} }} }},
      {{ "id": "item-content", "weight": 1, "component": {{ "Column": {{ "children": {{ "explicitList": ["item-title", "item-subtitle"] }} }} }} }},
      {{ "id": "item-title", "component": {{ "Text": {{ "usageHint": "h4", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "item-subtitle", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "subtitle" }} }} }} }},
      {{ "id": "item-action", "component": {{ "Icon": {{ "name": {{ "literalString": "arrowForward" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "default",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "[List Title]" }},
      {{ "key": "items", "valueMap": [
        {{ "key": "item1", "valueMap": [
          {{ "key": "icon", "valueString": "folder" }},
          {{ "key": "title", "valueString": "[Item Title]" }},
          {{ "key": "subtitle", "valueString": "[Item Subtitle]" }},
          {{ "key": "action", "valueString": "view_details" }}
        ] }}
      ] }}
    ]
  }} }}
]
---END SIMPLE_LIST_EXAMPLE---
"""
