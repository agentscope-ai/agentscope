# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for search filter form."""

SEARCH_FILTER_FORM_EXAMPLE = """
---BEGIN SEARCH_FILTER_FORM_EXAMPLE---
Use this template for search forms with filters.

[
  {{ "beginRendering": {{ "surfaceId": "search-form", "root": "search-column", "styles": {{ "primaryColor": "#2196F3", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "search-form",
    "components": [
      {{ "id": "search-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["search-title", "search-input-row", "filter-section", "search-button"] }} }} }} }},
      {{ "id": "search-title", "component": {{ "Text": {{ "usageHint": "h2", "text": {{ "literalString": "Search" }} }} }} }},
      {{ "id": "search-input-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["search-icon", "search-field"] }} }} }} }},
      {{ "id": "search-icon", "component": {{ "Icon": {{ "name": {{ "literalString": "search" }} }} }} }},
      {{ "id": "search-field", "weight": 1, "component": {{ "TextField": {{ "label": {{ "literalString": "Search" }}, "text": {{ "path": "searchQuery" }}, "hint": {{ "literalString": "Enter keywords..." }} }} }} }},
      {{ "id": "filter-section", "component": {{ "Column": {{ "children": {{ "explicitList": ["filter-title", "location-field", "category-field", "price-range-row"] }} }} }} }},
      {{ "id": "filter-title", "component": {{ "Text": {{ "usageHint": "h4", "text": {{ "literalString": "Filters" }} }} }} }},
      {{ "id": "location-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Location" }}, "text": {{ "path": "location" }} }} }} }},
      {{ "id": "category-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Category" }}, "text": {{ "path": "category" }} }} }} }},
      {{ "id": "price-range-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["min-price-field", "max-price-field"] }} }} }} }},
      {{ "id": "min-price-field", "weight": 1, "component": {{ "TextField": {{ "label": {{ "literalString": "Min Price" }}, "text": {{ "path": "minPrice" }}, "type": "number" }} }} }},
      {{ "id": "max-price-field", "weight": 1, "component": {{ "TextField": {{ "label": {{ "literalString": "Max Price" }}, "text": {{ "path": "maxPrice" }}, "type": "number" }} }} }},
      {{ "id": "search-button", "component": {{ "Button": {{ "child": "search-button-text", "primary": true, "action": {{ "name": "perform_search", "context": [ {{ "key": "query", "value": {{ "path": "searchQuery" }} }}, {{ "key": "location", "value": {{ "path": "location" }} }}, {{ "key": "category", "value": {{ "path": "category" }} }}, {{ "key": "minPrice", "value": {{ "path": "minPrice" }} }}, {{ "key": "maxPrice", "value": {{ "path": "maxPrice" }} }} ] }} }} }} }},
      {{ "id": "search-button-text", "component": {{ "Text": {{ "text": {{ "literalString": "Search" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "search-form",
    "path": "/",
    "contents": [
      {{ "key": "searchQuery", "valueString": "" }},
      {{ "key": "location", "valueString": "" }},
      {{ "key": "category", "valueString": "" }},
      {{ "key": "minPrice", "valueString": "" }},
      {{ "key": "maxPrice", "valueString": "" }}
    ]
  }} }}
]
---END SEARCH_FILTER_FORM_EXAMPLE---
"""
