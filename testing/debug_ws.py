#!/usr/bin/env python3
"""Dump raw WS messages to see exact structure."""
import asyncio, base64, json, os, time
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "config", "secrets.env")
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

def load_env(path):
    env = {}
    with open(path) as f:
        lines = f.read().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"): i += 1; continue
        if "=" not in line: i += 1; continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith('"') and not value.endswith('"'):
            parts = [value[1:]]
            i += 1
            while i < len(lines):
                if lines[i].strip().endswith('"'):
                    parts.append(lines[i].strip()[:-1]); break
                parts.append(lines[i]); i += 1
            value = "\n".join(parts)
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        env[key] = value; i += 1
    return env

ENV = load_env(ENV_PATH)
KEY_ID = ENV["KALSHI_API_KEY_ID"]
PK = serialization.load_pem_private_key(ENV["KALSHI_PRIVATE_KEY"].encode(), password=None)

def sign():
    ts = str(int(time.time() * 1000))
    msg = f"{ts}GET/trade-api/ws/v2"
    sig = PK.sign(msg.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": KEY_ID, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(), "KALSHI-ACCESS-TIMESTAMP": ts}

async def main():
    async with websockets.connect(WS_URL, additional_headers=sign()) as ws:
        await ws.send(json.dumps({
            "id": 1, "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta", "ticker"], "market_tickers": ["KXNCAAMBGAME-26JAN29SFPACHS-SFPA"]}
        }))
        n = 0
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type", msg.get("channel", "?"))
            if t in ("subscribed",):
                continue
            payload = msg.get("msg", msg)
            print(f"--- {t} ---")
            # Truncate long lists
            for k, v in payload.items():
                if k == "market_id": continue
                sv = json.dumps(v)
                if len(sv) > 200: sv = sv[:200] + "..."
                print(f"  {k}: {sv}")
            print()
            n += 1
            if n >= 12:
                break

asyncio.get_event_loop().run_until_complete(main())
