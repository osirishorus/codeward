#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time


def read_message():
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk
    length = 0
    for line in header.decode("ascii").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            break
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def write_message(msg):
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def load_fixture():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            return json.load(f)
    if os.environ.get("FAKE_LSP_FIXTURE"):
        return json.loads(os.environ["FAKE_LSP_FIXTURE"])
    return {}


def main():
    fixture = load_fixture()
    if fixture.get("sleep"):
        time.sleep(float(fixture["sleep"]))
    while True:
        msg = read_message()
        if msg is None:
            return
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": {"capabilities": {}}})
        elif method == "initialized":
            continue
        elif method == "textDocument/didOpen":
            continue
        elif method == "textDocument/definition":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": fixture.get("definition", [])})
        elif method == "textDocument/references":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": fixture.get("references", [])})
        elif method == "shutdown":
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": None})
        elif method == "exit":
            return
        elif msg_id is not None:
            write_message({"jsonrpc": "2.0", "id": msg_id, "result": None})


if __name__ == "__main__":
    main()
