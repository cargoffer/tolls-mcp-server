"""Tolls MCP Server — HTTP (SSE) + stdio dual-mode.

Makes toll cost calculation, toll gate lookup, and highway information
available to any MCP-compatible client.

Usage:
    # stdio mode (default, for Claude Desktop, Cursor, etc.)
    python server.py

    # HTTP SSE mode (for remote clients)
    python server.py --http --port 8080

Environment:
    TOLLS_API_URL     - Tolls API base (default: https://tolls.transcend.cargoffer.com)
    TOLLS_API_KEY     - Optional API key
    HOST / PORT       - For HTTP mode (default: 0.0.0.0:8080)
"""

import sys, json, argparse, os
from typing import Any, Dict
import httpx

# ── JSON-RPC helpers ─────────────────────────────────────
def jsonrpc_error(msg: dict, code: int, message: str, data=None) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"),
            "error": {"code": code, "message": message, "data": data}}

def jsonrpc_result(msg: dict, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}


# ── Toll MCP Server Logic ───────────────────────────────
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
        resp = self._client.get(f"{self.api_url}{path}", headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self._client.post(f"{self.api_url}{path}", headers=self._headers(), json=body)
        resp.raise_for_status()
        return resp.json()

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "calculate_toll",
                "description": "Calculate toll costs between two coordinates for a vehicle type. Returns route segments with toll costs, total cost, and duration.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "origin_lat": {"type": "number", "description": "Origin latitude"},
                        "origin_lng": {"type": "number", "description": "Origin longitude"},
                        "destination_lat": {"type": "number", "description": "Destination latitude"},
                        "destination_lng": {"type": "number", "description": "Destination longitude"},
                        "vehicle_type": {"type": "string", "enum": ["light", "truck", "bus", "motorcycle"], "default": "truck"},
                    },
                    "required": ["origin_lat", "origin_lng", "destination_lat", "destination_lng"],
                },
            },
            {
                "name": "list_operators",
                "description": "List all toll operators with their highways and countries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "country": {"type": "string", "description": "Filter by country code (ES, PT, FR)", "default": ""},
                    },
                },
            },
            {
                "name": "get_tariffs",
                "description": "Get toll tariffs for a specific operator and vehicle type.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operator": {"type": "string", "description": "Operator name (e.g. 'autema', 'brisa')"},
                        "vehicle_type": {"type": "string", "description": "Vehicle type (light/truck/bus/motorcycle)", "default": "truck"},
                    },
                    "required": ["operator"],
                },
            },
            {
                "name": "health_check",
                "description": "Check if the tolls API service is healthy.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

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


# ── HTTP (SSE) Mode ──────────────────────────────────────
def run_http(server: TollsMCPServer, host: str = "0.0.0.0", port: int = 8080):
    """Run MCP server over HTTP SSE transport."""
    from starlette.applications import Starlette
    from starlette.responses import Response, JSONResponse
    from starlette.routing import Route
    import uvicorn

    async def handle_request(request):
        body = await request.json()
        response = server.handle_message(body)
        if response is None:
            return Response(status_code=204)
        return JSONResponse(response)

    async def handle_get(request):
        return JSONResponse({
            "name": "tolls-mcp",
            "version": "1.0.0",
            "description": "Tolls Calculator MCP Server",
            "status": "running",
            "mode": "http-sse",
            "tools": server.tools,
        })

    async def health(request):
        try:
            h = server._get("/health")
            return JSONResponse({"status": "ok", "api": h})
        except Exception as e:
            return JSONResponse({"status": "error", "message": str(e)}, status_code=503)

    app = Starlette(debug=False, routes=[
        Route("/mcp", handle_request, methods=["POST"]),
        Route("/", handle_get, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ])
    uvicorn.run(app, host=host, port=port, log_level="info")


# ── Main ─────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Tolls MCP Server")
    parser.add_argument("--api-url", default=os.environ.get("TOLLS_API_URL", "https://tolls.transcend.cargoffer.com"))
    parser.add_argument("--api-key", default=os.environ.get("TOLLS_API_KEY", ""))
    parser.add_argument("--http", action="store_true", default=False, help="Run as HTTP SSE server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")), help="HTTP port")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="HTTP bind host")
    args = parser.parse_args()

    server = TollsMCPServer(api_url=args.api_url, api_key=args.api_key)

    if args.http:
        run_http(server, host=args.host, port=args.port)
        return

    # stdio mode (default)
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
