from openai import OpenAI
import os

api_key = "sk-33241ff45e3a454986732123b5e7214c"
base_url = "https://api.deepseek.com"

client = OpenAI(api_key=api_key, base_url=base_url)

try:
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Hello"},
        ],
        stream=False
    )
    print("Success! Response:", response.choices[0].message.content)
except Exception as e:
    print("Error:", e)
