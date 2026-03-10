# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for profile view."""

PROFILE_VIEW_WITH_IMAGE_EXAMPLE = """
---BEGIN PROFILE_VIEW_WITH_IMAGE_EXAMPLE---
Use this template to display user or entity profile information.

[
  {{ "beginRendering": {{ "surfaceId": "profile", "root": "profile-column", "styles": {{ "primaryColor": "#009688", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "profile",
    "components": [
      {{ "id": "profile-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["profile-header", "profile-card"] }} }} }} }},
      {{ "id": "profile-header", "component": {{ "Row": {{ "children": {{ "explicitList": ["avatar-image", "header-info"] }} }} }} }},
      {{ "id": "avatar-image", "component": {{ "Image": {{ "url": {{ "path": "avatarUrl" }}, "usageHint": "avatar" }} }} }},
      {{ "id": "header-info", "weight": 1, "component": {{ "Column": {{ "children": {{ "explicitList": ["profile-name", "profile-title"] }} }} }} }},
      {{ "id": "profile-name", "component": {{ "Text": {{ "usageHint": "h2", "text": {{ "path": "name" }} }} }} }},
      {{ "id": "profile-title", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "profile-card", "component": {{ "Card": {{ "child": "profile-details" }} }} }},
      {{ "id": "profile-details", "component": {{ "Column": {{ "children": {{ "explicitList": ["bio-section", "divider1", "contact-section", "divider2", "stats-section"] }} }} }} }},
      {{ "id": "bio-section", "component": {{ "Column": {{ "children": {{ "explicitList": ["bio-title", "bio-text"] }} }} }} }},
      {{ "id": "bio-title", "component": {{ "Text": {{ "usageHint": "h4", "text": {{ "literalString": "About" }} }} }} }},
      {{ "id": "bio-text", "component": {{ "Text": {{ "text": {{ "path": "bio" }} }} }} }},
      {{ "id": "divider1", "component": {{ "Divider": {{}} }} }},
      {{ "id": "contact-section", "component": {{ "Column": {{ "children": {{ "explicitList": ["email-row", "phone-row"] }} }} }} }},
      {{ "id": "email-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["email-icon", "email-text"] }} }} }} }},
      {{ "id": "email-icon", "component": {{ "Icon": {{ "name": {{ "literalString": "mail" }} }} }} }},
      {{ "id": "email-text", "weight": 1, "component": {{ "Text": {{ "text": {{ "path": "email" }} }} }} }},
      {{ "id": "phone-row", "component": {{ "Row": {{ "children": {{ "explicitList": ["phone-icon", "phone-text"] }} }} }} }},
      {{ "id": "phone-icon", "component": {{ "Icon": {{ "name": {{ "literalString": "phone" }} }} }} }},
      {{ "id": "phone-text", "weight": 1, "component": {{ "Text": {{ "text": {{ "path": "phone" }} }} }} }},
      {{ "id": "divider2", "component": {{ "Divider": {{}} }} }},
      {{ "id": "stats-section", "component": {{ "Row": {{ "children": {{ "explicitList": ["stat-1", "stat-2", "stat-3"] }} }} }} }},
      {{ "id": "stat-1", "weight": 1, "component": {{ "Column": {{ "children": {{ "explicitList": ["stat-1-value", "stat-1-label"] }} }} }} }},
      {{ "id": "stat-1-value", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "stat1Value" }} }} }} }},
      {{ "id": "stat-1-label", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "stat1Label" }} }} }} }},
      {{ "id": "stat-2", "weight": 1, "component": {{ "Column": {{ "children": {{ "explicitList": ["stat-2-value", "stat-2-label"] }} }} }} }},
      {{ "id": "stat-2-value", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "stat2Value" }} }} }} }},
      {{ "id": "stat-2-label", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "stat2Label" }} }} }} }},
      {{ "id": "stat-3", "weight": 1, "component": {{ "Column": {{ "children": {{ "explicitList": ["stat-3-value", "stat-3-label"] }} }} }} }},
      {{ "id": "stat-3-value", "component": {{ "Text": {{ "usageHint": "h3", "text": {{ "path": "stat3Value" }} }} }} }},
      {{ "id": "stat-3-label", "component": {{ "Text": {{ "usageHint": "caption", "text": {{ "path": "stat3Label" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "profile",
    "path": "/",
    "contents": [
      {{ "key": "name", "valueString": "[User Name]" }},
      {{ "key": "title", "valueString": "[Job Title or Role]" }},
      {{ "key": "avatarUrl", "valueString": "[Avatar Image URL]" }},
      {{ "key": "bio", "valueString": "[User biography or description]" }},
      {{ "key": "email", "valueString": "[Email Address]" }},
      {{ "key": "phone", "valueString": "[Phone Number]" }},
      {{ "key": "stat1Value", "valueString": "[Value]" }},
      {{ "key": "stat1Label", "valueString": "[Label]" }},
      {{ "key": "stat2Value", "valueString": "[Value]" }},
      {{ "key": "stat2Label", "valueString": "[Label]" }},
      {{ "key": "stat3Value", "valueString": "[Value]" }},
      {{ "key": "stat3Label", "valueString": "[Label]" }}
    ]
  }} }}
]
---END PROFILE_VIEW_WITH_IMAGE_EXAMPLE---
"""
