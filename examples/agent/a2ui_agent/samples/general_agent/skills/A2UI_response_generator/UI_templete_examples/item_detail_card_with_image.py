# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for item detail card with image."""

# Detail examples
ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE = """
---BEGIN ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE---
Use this template to display detailed information about a single item.

[
  {{ "beginRendering": {{ "surfaceId": "item-detail", "root": "detail-column", "styles": {{ "primaryColor": "#673AB7", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "item-detail",
    "components": [
      {{ "id": "detail-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["header-image", "detail-card"] }} }} }} }},
      {{ "id": "header-image", "component": {{ "Image": {{ "url": {{ "path": "imageUrl" }}, "usageHint": "header" }} }} }},
      {{ "id": "detail-card", "component": {{ "Card": {{ "child": "card-content" }} }} }},
      {{ "id": "card-content", "component": {{ "Column": {{ "children": {{ "explicitList": ["item-title", "item-subtitle", "divider1", "description-section", "divider2", "info-section", "action-row"] }} }} }} }},
      {{ "id": "item-title", "component": {{ "Text": {{ "usageHint": "h1", "text": {{ "path": "name" }} }} }} }},
      {{ "id": "item-subtitle", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "subtitle" }} }} }} }},
      {{ "id": "divider1", "component": {{ "Divider": {{}} }} }},
      {{ "id": "description-section", "component": {{ "Column": {{ "children": {{ "explicitList": ["description-title", "description-text"] }} }} }} }},
      {{ "id": "description-title", "component": {{ "Text": {{ "usageHint": "h4", "text": {{ "literalString": "Description" }} }} }} }},
      {{ "id": "description-text", "component": {{ "Text": {{ "text": {{ "path": "description" }} }} }} }},
      {{ "id": "divider2", "component": {{ "Divider": {{}} }} }},
      {{ "id": "info-section", "component": {{ "Column": {{ "children": {{ "explicitList": ["info-row-1", "info-row-2", "info-row-3"] }} }} }} }},
      {{ "id": "info-row-1", "component": {{ "Row": {{ "children": {{ "explicitList": ["info-icon-1", "info-text-1"] }} }} }} }},
      {{ "id": "info-icon-1", "component": {{ "Icon": {{ "name": {{ "literalString": "locationOn" }} }} }} }},
      {{ "id": "info-text-1", "weight": 1, "component": {{ "Text": {{ "text": {{ "path": "location" }} }} }} }},
      {{ "id": "info-row-2", "component": {{ "Row": {{ "children": {{ "explicitList": ["info-icon-2", "info-text-2"] }} }} }} }},
      {{ "id": "info-icon-2", "component": {{ "Icon": {{ "name": {{ "literalString": "phone" }} }} }} }},
      {{ "id": "info-text-2", "weight": 1, "component": {{ "Text": {{ "text": {{ "path": "phone" }} }} }} }},
      {{ "id": "info-row-3", "component": {{ "Row": {{ "children": {{ "explicitList": ["info-icon-3", "info-text-3"] }} }} }} }},
      {{ "id": "info-icon-3", "component": {{ "Icon": {{ "name": {{ "literalString": "star" }} }} }} }},
      {{ "id": "info-text-3", "weight": 1, "component": {{ "Text": {{ "text": {{ "path": "rating" }} }} }} }},
      {{ "id": "action-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["share-button", "primary-action-button"] }} }} }} }},
      {{ "id": "share-button", "weight": 1, "component": {{ "Button": {{ "child": "share-text", "action": {{ "name": "share", "context": [ {{ "key": "itemId", "value": {{ "path": "id" }} }} ] }} }} }} }},
      {{ "id": "share-text", "component": {{ "Text": {{ "text": {{ "literalString": "Share" }} }} }} }},
      {{ "id": "primary-action-button", "weight": 1, "component": {{ "Button": {{ "child": "action-text", "primary": true, "action": {{ "name": "select_item", "context": [ {{ "key": "itemId", "value": {{ "path": "id" }} }}, {{ "key": "itemName", "value": {{ "path": "name" }} }} ] }} }} }} }},
      {{ "id": "action-text", "component": {{ "Text": {{ "text": {{ "literalString": "Book Now" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "item-detail",
    "path": "/",
    "contents": [
      {{ "key": "id", "valueString": "[Item ID]" }},
      {{ "key": "name", "valueString": "[Item Name]" }},
      {{ "key": "subtitle", "valueString": "[Category or Type]" }},
      {{ "key": "imageUrl", "valueString": "[Header Image URL]" }},
      {{ "key": "description", "valueString": "[Detailed description of the item]" }},
      {{ "key": "location", "valueString": "[Address or Location]" }},
      {{ "key": "phone", "valueString": "[Phone Number]" }},
      {{ "key": "rating", "valueString": "[Rating] stars" }}
    ]
  }} }}
]
---END ITEM_DETAIL_CARD_EXAMPLE_WITH_IMAGE---
"""
