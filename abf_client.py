#!/usr/bin/env python3
"""Minimal stdlib-only MCP client for the Agent Bounty Feed.

Talks Streamable HTTP to https://agent-bounty-feed.dailydollar.workers.dev/mcp
(no auth required) and lists bounties an AI agent can claim.

Usage:
    python3 abf_client.py list            # all currently listed bounties
    python3 abf_client.py stats           # feed stats
    python3 abf_client.py new             # bounties seen since last run (uses .abf_state.json)
    python3 abf_client.py tools           # list MCP tools exposed by the server
"""
import json
import os
import sys
import urllib.request

MCP_URL = "https://agent-bounty-feed.dailydollar.workers.dev/mcp"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".abf_state.json")


def _post(payload: dict) -> dict:
    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "abf-stdlib-client/1.0.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
        sid = resp.headers.get("mcp-session-id")
    # Streamable HTTP may answer with SSE frames; parse the last data: line.
    for line in reversed(body.splitlines()):
        if line.startswith("data:"):
            return json.loads(line[5:].strip()), sid
    return json.loads(body), sid


class ABFClient:
    def __init__(self):
        self.sid = None

    def call(self, method: str, params: dict | None = None, _id: int = 1) -> dict:
        payload = {"jsonrpc": "2.0", "id": _id, "method": method}
        if params is not None:
            payload["params"] = params
        msg, sid = _post(payload)
        if sid:
            self.sid = sid
        if "error" in msg:
            raise RuntimeError(f"MCP error: {msg['error']}")
        return msg.get("result", {})

    def notify(self, method: str) -> None:
        req = urllib.request.Request(
            MCP_URL,
            data=json.dumps({"jsonrpc": "2.0", "method": method}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "User-Agent": "abf-stdlib-client/1.0.0",
                **({"mcp-session-id": self.sid} if self.sid else {}),
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception:
            pass  # notifications are best-effort

    def connect(self) -> None:
        self.call(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "abf-stdlib-client", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized")

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.call("tools/call", {"name": name, "arguments": arguments or {}}, _id=2)


def _items(result: dict):
    content = result.get("content", [])
    if content and isinstance(content[0], dict):
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return [{"raw": text}]
    return result if isinstance(result, list) else [result]


def _as_list(data) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "bounties", "results"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
        return [data]
    return []


def cmd_list(client: ABFClient) -> None:
    items = _as_list(_items(client.tool("list_bounties", {"exclude_requires": ["kyc", "stake", "bond"]})))
    print(f"{len(items)} bounties (excl. kyc/stake/bond):")
    for b in items:
        req = ",".join(b.get("requires", [])) or "none"
        print(f"  ${str(b.get('est_usd') or '?'):>5}  {str(b.get('id', '?')):28} {str(b.get('title', ''))[:70]}  [requires: {req}]")


def cmd_stats(client: ABFClient) -> None:
    print(json.dumps(_items(client.tool("get_feed_stats")), indent=2)[:1500])


def cmd_new(client: ABFClient) -> None:
    since = None
    if os.path.exists(STATE_FILE):
        since = json.load(open(STATE_FILE)).get("next_since")
    args = {"since": since} if since else {}
    res = client.tool("get_new_bounties", args)
    data = _items(res)
    items = _as_list(data)
    nxt = data.get("next_since") if isinstance(data, dict) else since
    print(f"{len(items)} new bounties since {since or 'beginning'}:")
    for b in items:
        print(f"  ${b.get('est_usd', '?'):>5}  {b.get('id', '?'):28} {b.get('title', '')[:70]}")
    if isinstance(data, dict) and data.get("next_since"):
        json.dump({"next_since": data["next_since"]}, open(STATE_FILE, "w"))
        print(f"(state saved: next_since={data['next_since']})")


def cmd_tools(client: ABFClient) -> None:
    res = client.call("tools/list", {}, _id=3)
    for t in res.get("tools", []):
        print(f"  {t['name']}: {t.get('description', '')[:90]}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    client = ABFClient()
    client.connect()
    {"list": cmd_list, "stats": cmd_stats, "new": cmd_new, "tools": cmd_tools}[cmd](client)


if __name__ == "__main__":
    main()
