import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def test_real_stdio_bridge_returns_images_and_forwards_legacy_actions():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/frame.jpg":
                payload, mime = b"synthetic jpeg", "image/jpeg"
            elif self.path == "/api/screen-text":
                payload = json.dumps({"source":"local_vision_ocr", "is_accessibility_tree":False, "rows":[]}).encode()
                mime = "application/json"
            else:
                payload = json.dumps({"agent": {"running": False, "task": "", "steps": []}}).encode()
                mime = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
            requests.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "hidagent.cli", "--url", f"http://127.0.0.1:{server.server_port}", "mcp"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {"iphone_screenshot", "iphone_tap", "delegate_task", "task_status", "stop_task", "iphone_command"} <= tools
            observed = await session.call_tool("iphone_read_text", {})
            assert not observed.isError
            assert json.loads(observed.content[0].text)["is_accessibility_tree"] is False
            assert all(item.type != "image" for item in observed.content)
            image = await session.call_tool("iphone_screenshot", {})
            assert not image.isError
            assert image.content[0].type == "image" and image.content[0].mimeType == "image/jpeg"
            for name, arguments in [("iphone_key", {"combo": "cmd+a"}),
                                    ("delegate_task", {"goal": "Open Clock"}), ("stop_task", {})]:
                assert not (await session.call_tool(name, arguments)).isError
            status = await session.call_tool("task_status", {})
            assert not status.isError and json.loads(status.content[0].text)["running"] is False
            receipt = await session.call_tool("iphone_command", {"goal":"输入hello", "wait_seconds":0})
            assert not receipt.isError
            assert any(item.type == "image" for item in receipt.content)
            assert any(item.type == "text" and "failed" in item.text for item in receipt.content)

    try:
        asyncio.run(asyncio.wait_for(scenario(), 20))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert requests == [("/api/key", {"text": "cmd+a"}), ("/api/agent", {"text": "Open Clock"}),
                        ("/api/agent/stop", None), ("/api/agent", {"text": "输入hello"})]
