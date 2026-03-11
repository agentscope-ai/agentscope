# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for single column list with images."""

# List examples
SINGLE_COLUMN_LIST_WITH_IMAGE_EXAMPLE = """
---BEGIN SINGLE_COLUMN_LIST_WITH_IMAGE_EXAMPLE---
Use this template when displaying a list of 5 or fewer items with detailed information.

[
  {{ "beginRendering": {{ "surfaceId": "default", "root": "root-column", "styles": {{ "primaryColor": "#FF0000", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "default",
    "components": [
      {{ "id": "root-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["title-heading", "item-list"] }} }} }} }},
      {{ "id": "title-heading", "component": {{ "Text": {{ "usageHint": "h1", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "item-list", "component": {{ "List": {{ "direction": "vertical", "children": {{ "template": {{ "componentId": "item-card-template", "dataBinding": "/items" }} }} }} }} }},
      {{ "id": "item-card-template", "component": {{ "Card": {{ "child": "card-layout" }} }} }},
      {{ "id": "card-layout", "component": {{ "Row": {{ "children": {{ "explicitList": ["template-image", "card-details"] }} }} }} }},
      {{ "id": "template-image", "weight": 1, "component": {{ "Image": {{ "url": {{ "path": "imageUrl" }} }} }} }},
      {{ "id": "card-details", "weight": 2, "component": {{ "Column": {{ "children": {{ "explicitList": ["template-name", "template-rating", "template-detail", "template-link", "template-action-button"] }} }} }} }},
      {{ "id": "template-name", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "name" }} }} }} }},
      {{ "id": "template-rating", "component": {{ "Text": {{ "text": {{ "path": "rating" }} }} }} }},
      {{ "id": "template-detail", "component": {{ "Text": {{ "text": {{ "path": "detail" }} }} }} }},
      {{ "id": "template-link", "component": {{ "Text": {{ "text": {{ "path": "infoLink" }} }} }} }},
      {{ "id": "template-action-button", "component": {{ "Button": {{ "child": "action-button-text", "primary": true, "action": {{ "name": "select_item", "context": [ {{ "key": "itemName", "value": {{ "path": "name" }} }}, {{ "key": "itemId", "value": {{ "path": "id" }} }} ] }} }} }} }},
      {{ "id": "action-button-text", "component": {{ "Text": {{ "text": {{ "literalString": "Select" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "default",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "[Your List Title]" }},
      {{ "key": "items", "valueMap": [
        {{ "key": "item1", "valueMap": [
          {{ "key": "id", "valueString": "1" }},
          {{ "key": "name", "valueString": "[Item Name]" }},
          {{ "key": "rating", "valueString": "[Rating]" }},
          {{ "key": "detail", "valueString": "[Detail Description]" }},
          {{ "key": "infoLink", "valueString": "[URL]" }},
          {{ "key": "imageUrl", "valueString": "[Image URL]" }}
        ] }}
      ] }}
    ]
  }} }}
]
---END SINGLE_COLUMN_LIST_WITH_IMAGE_EXAMPLE---
"""
