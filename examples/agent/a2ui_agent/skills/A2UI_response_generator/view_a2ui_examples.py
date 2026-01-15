# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""
A2UI Example Viewer - Tool for viewing A2UI UI template examples.

This script provides a way to retrieve A2UI UI template examples.

Usage:
    # Load specific template names
    python view_a2ui_examples.py --template_names SINGLE_COLUMN_LIST BOOKING_FORM
"""

# List examples
SINGLE_COLUMN_LIST_EXAMPLE = """
---BEGIN SINGLE_COLUMN_LIST_EXAMPLE---
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
---END SINGLE_COLUMN_LIST_EXAMPLE---
"""

TWO_COLUMN_LIST_EXAMPLE = """
---BEGIN TWO_COLUMN_LIST_EXAMPLE---
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
---END TWO_COLUMN_LIST_EXAMPLE---
"""

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
      {{ "id": "list-item-template", "component": {{ "Row": {{ "children": {{ "explicitList": ["item-icon", "item-content", "item-action"] }} }} }} }},
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
          {{ "key": "subtitle", "valueString": "[Item Subtitle]" }}
        ] }}
      ] }}
    ]
  }} }}
]
---END SIMPLE_LIST_EXAMPLE---
"""

# Form examples
BOOKING_FORM_EXAMPLE = """
---BEGIN BOOKING_FORM_EXAMPLE---
Use this template for booking, reservation, or registration forms.

[
  {{ "beginRendering": {{ "surfaceId": "booking-form", "root": "form-column", "styles": {{ "primaryColor": "#FF5722", "font": "Roboto" }} }} }},
  {{ "surfaceUpdate": {{
    "surfaceId": "booking-form",
    "components": [
      {{ "id": "form-column", "component": {{ "Column": {{ "children": {{ "explicitList": ["form-title", "form-image", "form-address", "party-size-field", "datetime-field", "notes-field", "submit-button"] }} }} }} }},
      {{ "id": "form-title", "component": {{ "Text": {{ "usageHint": "h2", "text": {{ "path": "title" }} }} }} }},
      {{ "id": "form-image", "component": {{ "Image": {{ "url": {{ "path": "imageUrl" }}, "usageHint": "mediumFeature" }} }} }},
      {{ "id": "form-address", "component": {{ "Text": {{ "text": {{ "path": "address" }} }} }} }},
      {{ "id": "party-size-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Party Size" }}, "text": {{ "path": "partySize" }}, "type": "number" }} }} }},
      {{ "id": "datetime-field", "component": {{ "DateTimeInput": {{ "label": {{ "literalString": "Date & Time" }}, "value": {{ "path": "reservationTime" }}, "enableDate": true, "enableTime": true }} }} }},
      {{ "id": "notes-field", "component": {{ "TextField": {{ "label": {{ "literalString": "Special Requests" }}, "text": {{ "path": "notes" }}, "multiline": true }} }} }},
      {{ "id": "submit-button", "component": {{ "Button": {{ "child": "submit-text", "primary": true, "action": {{ "name": "submit_booking", "context": [ {{ "key": "itemName", "value": {{ "path": "itemName" }} }}, {{ "key": "partySize", "value": {{ "path": "partySize" }} }}, {{ "key": "reservationTime", "value": {{ "path": "reservationTime" }} }}, {{ "key": "notes", "value": {{ "path": "notes" }} }} ] }} }} }} }},
      {{ "id": "submit-text", "component": {{ "Text": {{ "text": {{ "literalString": "Submit Reservation" }} }} }} }}
    ]
  }} }},
  {{ "dataModelUpdate": {{
    "surfaceId": "booking-form",
    "path": "/",
    "contents": [
      {{ "key": "title", "valueString": "Book [Item Name]" }},
      {{ "key": "address", "valueString": "[Address]" }},
      {{ "key": "itemName", "valueString": "[Item Name]" }},
      {{ "key": "imageUrl", "valueString": "[Image URL]" }},
      {{ "key": "partySize", "valueString": "2" }},
      {{ "key": "reservationTime", "valueString": "" }},
      {{ "key": "notes", "valueString": "" }}
    ]
  }} }}
]
---END BOOKING_FORM_EXAMPLE---
"""

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

# Confirmation examples
SUCCESS_CONFIRMATION_EXAMPLE = """
---BEGIN SUCCESS_CONFIRMATION_EXAMPLE---
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
---END SUCCESS_CONFIRMATION_EXAMPLE---
"""

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

# Detail examples
ITEM_DETAIL_CARD_EXAMPLE = """
---BEGIN ITEM_DETAIL_CARD_EXAMPLE---
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
---END ITEM_DETAIL_CARD_EXAMPLE---
"""

PROFILE_VIEW_EXAMPLE = """
---BEGIN PROFILE_VIEW_EXAMPLE---
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
---END PROFILE_VIEW_EXAMPLE---
"""


# Template name to example mapping
TEMPLATE_MAP = {
    "SINGLE_COLUMN_LIST": SINGLE_COLUMN_LIST_EXAMPLE,
    "TWO_COLUMN_LIST": TWO_COLUMN_LIST_EXAMPLE,
    "SIMPLE_LIST": SIMPLE_LIST_EXAMPLE,
    "BOOKING_FORM": BOOKING_FORM_EXAMPLE,
    "SEARCH_FILTER_FORM": SEARCH_FILTER_FORM_EXAMPLE,
    "CONTACT_FORM": CONTACT_FORM_EXAMPLE,
    "SUCCESS_CONFIRMATION": SUCCESS_CONFIRMATION_EXAMPLE,
    "ERROR_MESSAGE": ERROR_MESSAGE_EXAMPLE,
    "INFO_MESSAGE": INFO_MESSAGE_EXAMPLE,
    "ITEM_DETAIL_CARD": ITEM_DETAIL_CARD_EXAMPLE,
    "PROFILE_VIEW": PROFILE_VIEW_EXAMPLE,
}


def view_a2ui_examples(template_names: list[str]) -> str:
    """
    View A2UI UI template examples for generating UI responses.

    Args:
        template_names: Specific template names to load. Options:
                       - SINGLE_COLUMN_LIST, TWO_COLUMN_LIST, SIMPLE_LIST
                       - BOOKING_FORM, SEARCH_FILTER_FORM, CONTACT_FORM
                       - SUCCESS_CONFIRMATION, ERROR_MESSAGE, INFO_MESSAGE
                       - ITEM_DETAIL_CARD, PROFILE_VIEW

    Returns:
        The requested template examples.

    Examples:
        # Load specific templates
        >>> view_a2ui_examples(template_names=["BOOKING_FORM"])
        >>> view_a2ui_examples(template_names=["SINGLE_COLUMN_LIST", "BOOKING_FORM"])
    """
    if not template_names:
        raise ValueError("template_names is required and cannot be empty")

    examples = []
    for name in template_names:
        if name in TEMPLATE_MAP:
            examples.append(TEMPLATE_MAP[name])
        else:
            raise ValueError(f"Unknown template name: {name}")

    return f"""
## A2UI Templates: {', '.join(template_names)}

{chr(10).join(examples)}

---
Adapt these templates to your specific data and styling requirements.
"""


# Tool metadata for AgentScope registration
TOOL_METADATA = {
    "name": "view_a2ui_examples",
    "description": "View A2UI UI template examples for generating UI responses.",
    "parameters": {
        "type": "object",
        "properties": {
            "template_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific template names to load",
            },
        },
        "required": ["template_names"],
    },
}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="View A2UI UI template examples for generating UI responses.",
    )
    parser.add_argument(
        "--template_names",
        type=str,
        nargs="+",
        required=True,
        help="Specific template names to load (space-separated)",
    )

    args = parser.parse_args()

    res = view_a2ui_examples(template_names=args.template_names)
    print(res)
