"""Tolls MCP Server — Exposes the TRANSCEND Tolls Calculator API as MCP tools.

Makes toll cost calculation, toll gate lookup, and highway information
available to any MCP-compatible client (Claude Desktop, Cursor, Cline, etc.).

Usage:
    pip install httpx mcp
    python server.py
"""

import sys, json, argparse, uuid, os
from typing import Any
import httpx

# ── JSON-RPC ───────────────────────────────────────────
def jsonrpc_error(msg: dict, code: int, message: str, data=None) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"),
            "error": {"code": code, "message": message, "data": data}}

def jsonrpc_result(msg: dict, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}

# ── Server ──────────────────────────────────────────────
class TollsMCPServer:
    def __init__(self, api_url: str, api_key: str | None = None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=60.0)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Access-Token"] = self.api_key
        return h

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self._client.get(
            f"{self.api_url}{path}",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self._client.post(
            f"{self.api_url}{path}",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Tool Definitions ────────────────────────────────
    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "calculate_toll",
                "description": "Calculate toll costs between two coordinates for a vehicle type. Returns route segments with toll costs, total cost, and duration. Use this when a user asks about toll prices for a specific route.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "origin_lat": {"type": "number", "description": "Origin latitude"},
                        "origin_lng": {"type": "number", "description": "Origin longitude"},
                        "destination_lat": {"type": "number", "description": "Destination latitude"},
                        "destination_lng": {"type": "number", "description": "Destination longitude"},
                        "vehicle_type": {"type": "string", "enum": ["light", "truck", "bus", "motorcycle"], "default": "truck", "description": "Vehicle type for toll class"},
                    },
                    "required": ["origin_lat", "origin_lng", "destination_lat", "destination_lng"],
                },
            },
            {
                "name": "list_operators",
                "description": "List all toll operators with their highways and countries. Returns operator names, highways they manage, and supported countries. Use this to discover which toll operators are available.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string", "description": "Filter by country code (ES, PT, FR)", "default": ""},
                    },
                },
            },
            {
                "name": "get_tariffs",
                "description": "Get toll tariffs for a specific operator and vehicle type. Returns detailed tariff data including entry/exit points and prices. Use this when a user wants to see specific toll rates for a highway.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operator": {"type": "string", "description": "Operator name (e.g. 'autema', 'atlandes', 'brisa')"},
                        "vehicle_type": {"type": "string", "description": "Vehicle type (light/truck/bus/motorcycle)", "default": "truck"},
                    },
                    "required": ["operator"],
                },
            },
            {
                "name": "health_check",
                "description": "Check if the tolls API service is healthy and responsive. Returns service status and database connection state.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    # ── Tool Handlers ────────────────────────────────────
    _ROUTES = {
        "calculate_toll": "POST",
        "list_operators": "GET",
        "get_tariffs": "GET",
        "health_check": "GET",
    }

    def _handle_tool_call(self, name: str, args: dict) -> Any:
        if name == "calculate_toll":
            body = {
                "origin": [args["origin_lat"], args["origin_lng"]],
                "destination": [args["destination_lat"], args["destination_lng"]],
                "vehicleType": args.get("vehicle_type", "truck"),
            }
            return self._post("/api/tolls/calculate", body)

        elif name == "list_operators":
            params = {}
            if args.get("country"):
                params["country"] = args["country"]
            return self._get("/api/tolls/operators", params)

        elif name == "get_tariffs":
            params = {"operator": args["operator"]}
            if args.get("vehicle_type"):
                params["vehicleType"] = args["vehicle_type"]
            return self._get("/api/tolls/tariffs", params)

        elif name == "health_check":
            return self._get("/health")

        raise ValueError(f"Unknown tool: {name}")

    # ── JSON-RPC Message Handling ────────────────────────
    def handle_message(self, msg: dict) -> dict | None:
        method = msg.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return jsonrpc_result(msg, {
                "protocolVersion": "0.1.0",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tolls-mcp", "version": "1.0.0"},
            })
        if method == "tools/list":
            return jsonrpc_result(msg, {"tools": self.tools})
        if method == "tools/call":
            name = msg.get("params", {}).get("name", "")
            args = msg.get("params", {}).get("arguments", {})
            try:
                result = self._handle_tool_call(name, args)
                return jsonrpc_result(msg, {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}],
                })
            except httpx.HTTPStatusError as e:
                return jsonrpc_result(msg, {
                    "content": [{"type": "text", "text": f"API error {e.response.status_code}: {e.response.text[:500]}"}],
                    "isError": True,
                })
            except Exception as e:
                return jsonrpc_result(msg, {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                })
        if method == "ping":
            return jsonrpc_result(msg, {})
        return jsonrpc_error(msg, -32601, f"Method not found: {method}")

# ── Main ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Tolls MCP Server")
    parser.add_argument("--api-url", default=os.environ.get("TOLLS_API_URL", "https://tolls.transcend.cargoffer.com"))
    parser.add_argument("--api-key", default=os.environ.get("TOLLS_API_KEY", ""))
    parser.add_argument("--stdio", action="store_true", default=True, help="Run as stdio server")
    args = parser.parse_args()
    server = TollsMCPServer(api_url=args.api_url, api_key=args.api_key)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            response = server.handle_message(msg)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass
        except (BrokenPipeError, EOFError):
            break

if __name__ == "__main__":
    main()
