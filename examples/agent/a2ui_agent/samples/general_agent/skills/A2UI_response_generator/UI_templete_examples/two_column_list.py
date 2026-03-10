# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for two column grid list with images."""

TWO_COLUMN_LIST_WITH_IMAGE_EXAMPLE = """
---BEGIN TWO_COLUMN_LIST_WITH_IMAGE_EXAMPLE---
Use this template when displaying more than 5 items in a grid layout.

[
  {{ "beginRendering": {{ "surfaceId": "default", "root": "root-column", "styles": {{ "primaryColor": "#FF0000", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "default",
    "components": [
      {{ "id": "root-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["title-heading", "item-row-1", "item-row-2"] }} }} }} }},
      {{ "id": "title-heading", "component": {{ "Text": {{ "usageHint": "h1", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "item-row-1", "component": {{ "Row": {{ "children": {{ "explicitList": ["item-card-1", "item-card-2"] }} }} }} }},
      {{ "id": "item-row-2", "component": {{ "Row": {{ "children": {{ "explicitList": ["item-card-3", "item-card-4"] }} }} }} }},
      {{ "id": "item-card-1", "weight": 1, "component": {{ "Card": {{ "child": "card-layout-1" }} }} }},
      {{ "id": "card-layout-1", "component": {{ "Column": {{ "children": {{ "explicitList": ["template-image-1", "card-details-1"] }} }} }} }},
      {{ "id": "template-image-1", "component": {{ "Image": {{ "url": {{ "path": "/items/0/imageUrl" }}, "width": "100%" }} }} }},
      {{ "id": "card-details-1", "component": {{ "Column": {{ "children": {{ "explicitList": ["template-name-1", "template-rating-1", "template-action-button-1"] }} }} }} }},
      {{ "id": "template-name-1", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "/items/0/name" }} }} }} }},
      {{ "id": "template-rating-1", "component": {{ "Text": {{ "text": {{ "path": "/items/0/rating" }} }} }} }},
      {{ "id": "template-action-button-1", "component": {{ "Button": {{ "child": "action-text-1", "action": {{ "name": "select_item", "context": [ {{ "key": "itemName", "value": {{ "path": "/items/0/name" }} }} ] }} }} }} }},
      {{ "id": "action-text-1", "component": {{ "Text": {{ "text": {{ "literalString": "Select" }} }} }} }},
      {{ "id": "item-card-2", "weight": 1, "component": {{ "Card": {{ "child": "card-layout-2" }} }} }},
      {{ "id": "card-layout-2", "component": {{ "Column": {{ "children": {{ "explicitList": ["template-image-2", "card-details-2"] }} }} }} }},
      {{ "id": "template-image-2", "component": {{ "Image": {{ "url": {{ "path": "/items/1/imageUrl" }}, "width": "100%" }} }} }},
      {{ "id": "card-details-2", "component": {{ "Column": {{ "children": {{ "explicitList": ["template-name-2", "template-rating-2", "template-action-button-2"] }} }} }} }},
      {{ "id": "template-name-2", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "/items/1/name" }} }} }} }},
      {{ "id": "template-rating-2", "component": {{ "Text": {{ "text": {{ "path": "/items/1/rating" }} }} }} }},
      {{ "id": "template-action-button-2", "component": {{ "Button": {{ "child": "action-text-2", "action": {{ "name": "select_item", "context": [ {{ "key": "itemName", "value": {{ "path": "/items/1/name" }} }} ] }} }} }} }},
      {{ "id": "action-text-2", "component": {{ "Text": {{ "text": {{ "literalString": "Select" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "default",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "[Your Grid Title]" }},
      {{ "key": "items", "valueMap": [
        {{ "key": "0", "valueMap": [
          {{ "key": "name", "valueString": "[Item 1 Name]" }},
          {{ "key": "rating", "valueString": "[Rating]" }},
          {{ "key": "imageUrl", "valueString": "[Image URL]" }}
        ] }},
        {{ "key": "1", "valueMap": [
          {{ "key": "name", "valueString": "[Item 2 Name]" }},
          {{ "key": "rating", "valueString": "[Rating]" }},
          {{ "key": "imageUrl", "valueString": "[Image URL]" }}
        ] }}
      ] }}
    ]
  }} }}
]
---END TWO_COLUMN_LIST_WITH_IMAGE_EXAMPLE---
"""
