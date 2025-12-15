
import asyncio
from agentscope.tool import web_search

async def main():
    print("Testing Web Search Tool (DuckDuckGo)...")
    res = await web_search("agentscope python library")
    print(res.content[0]['text'][:500] + "...")

if __name__ == "__main__":
    asyncio.run(main())
