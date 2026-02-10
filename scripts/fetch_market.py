"""Fetch live market data from Kalshi API"""

import base64
import json
import time
import sys
import os

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Load from env file
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "secrets.env")


def load_env(path):
    """Parse secrets.env, handling multiline PEM values"""
    env = {}
    with open(path) as f:
        content = f.read()

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # skip comments and blanks
        if not line or line.startswith("#"):
            i += 1
            continue

        if "=" not in line:
            i += 1
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Handle multiline quoted value
        if value.startswith('"') and not value.endswith('"'):
            parts = [value[1:]]  # strip opening quote
            i += 1
            while i < len(lines):
                if lines[i].strip().endswith('"'):
                    parts.append(lines[i].strip()[:-1])  # strip closing quote
                    break
                parts.append(lines[i])
                i += 1
            value = "\n".join(parts)
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]

        env[key] = value
        i += 1

    return env


def sign_request(private_key, method, path):
    """Generate Kalshi auth headers"""
    timestamp_ms = str(int(time.time() * 1000))
    message = f"{timestamp_ms}{method}{path}"

    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type": "application/json",
    }


def api_get(path, params=None):
    """Make authenticated GET request"""
    url = f"{BASE_URL}{path}"
    headers = sign_request(PRIVATE_KEY, "GET", f"/trade-api/v2{path}")
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


# ── Load credentials ────────────────────────────────────────────────────────
env = load_env(ENV_PATH)
API_KEY_ID = env["KALSHI_API_KEY_ID"]
pem_str = env["KALSHI_PRIVATE_KEY"]
PRIVATE_KEY = serialization.load_pem_private_key(pem_str.encode("utf-8"), password=None)


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "KXATPMATCH-26FEB09SVADAV"
    event_ticker = "-".join(ticker.split("-")[:2])  # e.g. KXATPMATCH-26FEB09

    print(f"{'='*60}")
    print(f"  Fetching live data for: {ticker}")
    print(f"{'='*60}\n")

    # 1. Get market info
    print("[1] Market Info")
    print("-" * 40)
    try:
        data = api_get(f"/markets/{ticker}")
        m = data.get("market", data)
        print(f"  Title:           {m.get('title', m.get('subtitle', 'N/A'))}")
        print(f"  Ticker:          {m.get('ticker')}")
        print(f"  Event:           {m.get('event_ticker')}")
        print(f"  Status:          {m.get('status')}")
        print(f"  Yes Bid:         {m.get('yes_bid')}c  (${m.get('yes_bid', 0)/100:.2f})")
        print(f"  Yes Ask:         {m.get('yes_ask')}c  (${m.get('yes_ask', 0)/100:.2f})")
        print(f"  Last Price:      {m.get('last_price')}c  (${(m.get('last_price') or 0)/100:.2f})")
        print(f"  Volume:          {m.get('volume', 'N/A')}")
        print(f"  Open Interest:   {m.get('open_interest', 'N/A')}")
        print(f"  Close Time:      {m.get('close_time', 'N/A')}")
        print(f"  Result:          {m.get('result', 'pending')}")
    except requests.HTTPError as e:
        print(f"  Error: {e.response.status_code} - {e.response.text}")
        # If exact ticker fails, try fetching the event
        print(f"\n  Trying event lookup: {event_ticker}")
        try:
            data = api_get("/markets", params={"event_ticker": event_ticker, "limit": 20})
            markets = data.get("markets", [])
            if markets:
                print(f"  Found {len(markets)} markets in event:\n")
                for m in markets:
                    yes_bid = m.get("yes_bid", 0) or 0
                    yes_ask = m.get("yes_ask", 0) or 0
                    print(f"    {m.get('ticker')}")
                    print(f"      {m.get('title', m.get('subtitle', ''))}")
                    print(f"      Yes: {yes_bid}c bid / {yes_ask}c ask  |  Vol: {m.get('volume', 0)}  |  Status: {m.get('status')}")
                    print()
                # Use first active market for orderbook
                active = [m for m in markets if m.get("status") == "open"]
                if active:
                    ticker = active[0]["ticker"]
                    print(f"  Using {ticker} for orderbook...\n")
                else:
                    return
            else:
                print("  No markets found for this event.")
                return
        except Exception as e2:
            print(f"  Event lookup also failed: {e2}")
            return

    # 2. Get orderbook
    print(f"\n[2] Order Book ({ticker})")
    print("-" * 40)
    try:
        ob = api_get(f"/markets/{ticker}/orderbook")
        orderbook = ob.get("orderbook", ob)

        yes_levels = orderbook.get("yes", [])
        no_levels = orderbook.get("no", [])

        print("  YES side (bids):")
        if yes_levels:
            for price, size in sorted(yes_levels, reverse=True)[:10]:
                print(f"    {price}c (${price/100:.2f})  x  {size} contracts")
        else:
            print("    (empty)")

        print("  NO side (asks):")
        if no_levels:
            for price, size in sorted(no_levels, reverse=True)[:10]:
                print(f"    {price}c (${price/100:.2f})  x  {size} contracts")
        else:
            print("    (empty)")
    except Exception as e:
        print(f"  Error fetching orderbook: {e}")

    # 3. Get event-level info
    print(f"\n[3] Event: {event_ticker}")
    print("-" * 40)
    try:
        data = api_get(f"/events/{event_ticker}")
        ev = data.get("event", data)
        print(f"  Title:     {ev.get('title', 'N/A')}")
        print(f"  Category:  {ev.get('category', 'N/A')}")
        print(f"  Status:    {ev.get('status', 'N/A')}")
        markets = ev.get("markets", [])
        if markets:
            print(f"  Markets:   {len(markets)}")
            for m in markets:
                yes_bid = m.get("yes_bid", 0) or 0
                print(f"    - {m.get('ticker')}: {m.get('title', m.get('subtitle', ''))} (Yes: {yes_bid}c)")
    except Exception as e:
        print(f"  Error: {e}")

    print(f"\n{'='*60}")
    print("  Done.")


if __name__ == "__main__":
    main()
