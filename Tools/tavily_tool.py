from tavily import TavilyClient
import os
from dotenv import load_dotenv

load_dotenv()
client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

def tavily_search(query):
    response = client.search(
        query=query,
        max_results=5
    )
    results = []


    for i, r in enumerate(response["results"]):
        title = r.get("title","unknown")
        url = r.get("url","")
        snippet = r.get("content","").strip()

        # Keep only the first 300 characters of the snippet
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "..."

        results.append(f"{i+1}. {title}\nURL: {url}\nSnippet: {snippet}\n")

    return "\n\n".join(results)

