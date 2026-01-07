# -*- coding: utf-8 -*-
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tools for restaurant finder agent including search and booking."""
import json
import logging
import os
from typing import Any

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

logger = logging.getLogger(__name__)

# Global state to store base_url
_base_url_state: dict[str, Any] = {}


def set_base_url(base_url: str) -> None:
    """Set the base URL for image URLs in restaurant data.

    Args:
        base_url: The base URL to replace localhost URLs with.
    """
    _base_url_state["base_url"] = base_url


def book_restaurants(
    restaurant_name: str,
    phone_number: str,
    date: str,
    time: str,
    party_size: int,
) -> ToolResponse:
    """Book a restaurant.

    Args:
        restaurant_name: The name of the restaurant to book.
        phone_number: The phone number to book the restaurant.
        date: The date to book the restaurant.
        time: The time to book the restaurant.
        party_size: The party size to book the restaurant.

    Returns:
        ToolResponse: A JSON string containing the booking details.
    """

    import random

    logger.info(
        "book_restaurants: restaurant_name: %s, phone_number: %s, "
        "date: %s, time: %s, party_size: %s",
        restaurant_name,
        phone_number,
        date,
        time,
        party_size,
    )

    if random.random() < 0.5:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps(
                        {
                            "message": (
                                "Booking failed, because the restaurant "
                                "is not available at the given time, "
                                "please try again with a different time"
                            ),
                        },
                    ),
                ),
            ],
        )
    else:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=json.dumps({"message": "Booking successful"}),
                ),
            ],
        )


def get_restaurants(
    cuisine: str,
    location: str,
    count: int = 5,
) -> ToolResponse:
    """Get restaurant information based on cuisine and location.

    This tool searches for restaurants in the restaurant database by cuisine
    and location, and returns up to the specified count of results.

    Args:
        cuisine (str):
            The cuisine type to search for (e.g., "Chinese", "Italian").
        location (str):
            The location to search for (e.g., "New York", "NY").
        count (int, optional):
            The maximum number of restaurants to return. Defaults to 5.

    Returns:
        ToolResponse: A JSON string containing the list of matching
            restaurants.
    """
    logger.info("--- TOOL CALLED: get_restaurants ---")
    logger.info("  - Cuisine: %s", cuisine)
    logger.info("  - Location: %s", location)
    logger.info("  - Count: %s", count)

    results = []
    try:
        script_dir = os.path.dirname(__file__)
        file_path = os.path.join(script_dir, "restaurant_data.json")
        with open(file_path, encoding="utf-8") as f:
            restaurant_data_str = f.read()
            # Replace base URL if set
            base_url = _base_url_state.get("base_url")
            if base_url:
                restaurant_data_str = restaurant_data_str.replace(
                    "http://localhost:10002",
                    base_url,
                )
                logger.info("Updated base URL from state: %s", base_url)
            all_restaurants = json.loads(restaurant_data_str)

        cuisine_lower = cuisine.lower() if cuisine else ""
        location_lower = location.lower() if location else ""

        # Filter by location (check if location appears in address)
        if location_lower:
            results = [
                restaurant
                for restaurant in all_restaurants
                if location_lower in restaurant["address"].lower()
            ]
        else:
            results = all_restaurants

        # Filter by cuisine (check if cuisine appears in name or detail)
        if cuisine_lower:
            results = [
                restaurant
                for restaurant in results
                if (
                    cuisine_lower in restaurant["name"].lower()
                    or cuisine_lower in restaurant["detail"].lower()
                )
            ]

        # Limit results by count
        results = results[:count]

        logger.info(
            "  - Success: Found %s matching restaurants.",
            len(results),
        )

    except FileNotFoundError:
        logger.error(
            "  - Error: restaurant_data.json not found at %s",
            file_path,
        )
    except json.JSONDecodeError:
        logger.error("  - Error: Failed to decode JSON from %s", file_path)

    return ToolResponse(
        content=[TextBlock(type="text", text=json.dumps(results))],
    )
