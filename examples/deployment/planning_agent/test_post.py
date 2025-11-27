# -*- coding: utf-8 -*-
"""Send the post request to get the response from the agent"""

import requests


def send_post(user_query: str) -> None:
    """Send the post request to the agent endpoint and print the response."""
    res = requests.post(
        url="http://127.0.0.1:5000/chat_endpoint",
        json={
            "user_id": "test_user",
            "session_id": "test_session",
            "user_input": user_query,
        },
        stream=True,
    )

    res.raise_for_status()

    for chunk in res.iter_content(chunk_size=None):
        if chunk:
            print(repr(chunk.decode("utf-8")))


print("The first request response:")
# We first tell who we are in the first request
send_post("Hi, Alice!")

print("\n\nThe second request response:")
# Test if the session is loaded correctly
send_post("Do you know my name?")

print("\n\nThe third request response:")
send_post("Help me to write a hello world in Python")
