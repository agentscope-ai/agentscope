# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""A2UI template example for booking form."""

BOOKING_FORM_WITH_IMAGE = """
---BEGIN BOOKING_FORM_WITH_IMAGE_EXAMPLE---
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
---END BOOKING_FORM_WITH_IMAGE_EXAMPLE---
"""
