"""Debug script: fetch a Zillow page via Scrapfly and show parsing results.

Usage:
    export SCRAPFLY_API_KEY=your_key
    python scripts/debug_scrapfly.py "14933 SE 45th Place, Bellevue, WA 98006"
"""

import asyncio
import os
import sys

import httpx


async def main():
    address = sys.argv[1] if len(sys.argv) > 1 else "14933 SE 45th Place, Bellevue, WA 98006"
    key = os.environ.get("SCRAPFLY_API_KEY", "")
    if not key:
        print("ERROR: Set SCRAPFLY_API_KEY environment variable")
        sys.exit(1)

    # Build Zillow URL
    slug = address.replace(" ", "-").replace(",", "").replace(".", "")
    url = f"https://www.zillow.com/homes/{slug}_rb/"
    print(f"Target URL: {url}")
    print(f"API Key: {key[:8]}...{key[-4:]}")
    print()

    for render_js in [True, False]:
        print(f"{'='*60}")
        print(f"Trying render_js={render_js}")
        print(f"{'='*60}")

        params = {
            "key": key,
            "url": url,
            "asp": "true",
            "render_js": str(render_js).lower(),
            "country": "US",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://api.scrapfly.io/scrape", params=params)

        print(f"Scrapfly status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error: {resp.text[:500]}")
            continue

        data = resp.json()
        result = data.get("result", {})
        print(f"Upstream status: {result.get('status_code')}")
        content = result.get("content", "")
        print(f"HTML length: {len(content)} chars")
        print()

        # Check __NEXT_DATA__
        if "__NEXT_DATA__" in content:
            print("[OK] __NEXT_DATA__ found!")
            import json
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, "lxml")
            script = soup.find("script", {"id": "__NEXT_DATA__"})
            if script and script.string:
                nd = json.loads(script.string)
                print(f"  Top keys: {list(nd.keys())}")
                props = nd.get("props", {})
                pp = props.get("pageProps", {})
                print(f"  pageProps keys: {list(pp.keys())[:10]}")
                cp = pp.get("componentProps", {})
                if cp:
                    print(f"  componentProps keys: {list(cp.keys())[:10]}")
                    gdc = cp.get("gdpClientCache")
                    if gdc:
                        if isinstance(gdc, str):
                            gdc = json.loads(gdc)
                        print(f"  gdpClientCache keys: {list(gdc.keys())[:5]}")
                        for k, v in list(gdc.items())[:1]:
                            if isinstance(v, str):
                                v = json.loads(v)
                            prop = v.get("property", {}) if isinstance(v, dict) else {}
                            print(f"    First entry property keys: {list(prop.keys())[:10]}")
                            print(f"    zpid={prop.get('zpid')}")
                            print(f"    zestimate={prop.get('zestimate')}")
                            print(f"    rentZestimate={prop.get('rentZestimate')}")
                    else:
                        print("  NO gdpClientCache")
                        # Show what's available
                        for k2, v2 in pp.items():
                            if isinstance(v2, dict):
                                print(f"  pageProps.{k2} keys: {list(v2.keys())[:8]}")
                else:
                    print("  NO componentProps")
                    for k2, v2 in pp.items():
                        if isinstance(v2, dict):
                            print(f"  pageProps.{k2} keys: {list(v2.keys())[:8]}")
                        elif isinstance(v2, str) and len(v2) > 100:
                            print(f"  pageProps.{k2}: (string, {len(v2)} chars)")
        else:
            print("[MISS] No __NEXT_DATA__ in HTML")

        # Check for zestimate in raw text
        import re
        patterns = {
            '"zestimate":NNN': re.findall(r'"zestimate"\s*:\s*\d+', content[:100000]),
            '$NNN Zestimate': re.findall(r'\$[\d,]+\s*Zestimate', content[:100000], re.IGNORECASE),
            'Zestimate...$NNN': re.findall(r'Zestimate[^$]{0,50}\$[\d,]+', content[:100000], re.IGNORECASE),
        }
        print()
        for name, matches in patterns.items():
            if matches:
                print(f"[OK] {name}: {matches[:3]}")
            else:
                print(f"[MISS] {name}: not found")

        # Save HTML
        outfile = f"/tmp/scrapfly_render_js_{str(render_js).lower()}.html"
        with open(outfile, "w") as f:
            f.write(content)
        print(f"\nSaved to: {outfile}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
