import requests, os
from dotenv import load_dotenv
load_dotenv()

url = f"https://{os.environ['SHOPIFY_STORE']}/admin/api/2025-01/graphql.json"
headers = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": os.environ["SHOPIFY_TOKEN"]
}

query = """
{
  metaobjects(type: "gold_rate", first: 10) {
    edges {
      node {
        id
        handle
        fields {
          key
          value
        }
      }
    }
  }
}
"""

r = requests.post(url, headers=headers, json={"query": query})
import json
print(json.dumps(r.json(), indent=2))