"""Tolls MCP Server — HTTP (SSE) + stdio dual-mode.

Makes toll cost calculation, operator listing, tariff lookup and health status
available to any MCP-compatible client.

Two backends (TOLLS_BACKEND env or --backend):

  saas (DEFAULT)  → Tolls SaaS API (api.tolls.cargoffer.com)
                    Paths: /api/tolls/calculate, /api/operators, /api/tariffs
                    Auth:  Authorization: Bearer <TOLLS_API_KEY>  (tk_live_...)

  engine          → TRANSCEND tolls_calculator module directly (for LOCAL/RELEASE
                    testing only; never point this at PROD)
                    Paths: /api/tolls/calculate, /api/tolls/operators, /api/tolls/tariffs
                    Auth:  x-api-key: <TOLLS_API_KEY>  (INTERNAL_API_KEY)

Usage:
    # stdio mode (default, for Claude Desktop, Cursor, etc.) — SaaS backend
    python server.py

    # local testing against the RELEASE engine (no SaaS needed)
    TOLLS_BACKEND=engine TOLLS_API_KEY=<internal-key> python server.py

    # HTTP SSE mode (for remote clients / Coolify deploy)
    TOLLS_API_URL=https://api.tolls.cargoffer.com TOLLS_API_KEY=tk_live_... \
        python server.py --http --port 8080

Environment:
    TOLLS_BACKEND  - 'saas' (default) | 'engine'
    TOLLS_API_URL  - override backend base URL
    TOLLS_API_KEY  - API key (Bearer for saas, x-api-key for engine)
    HOST / PORT    - For HTTP mode (default: 0.0.0.0:8080)
"""

from __future__ import annotations

import sys, json, argparse, os
from typing import Any, Dict, Optional
import httpx

# ── JSON-RPC helpers ─────────────────────────────────────
def jsonrpc_error(msg: dict, code: int, message: str, data=None) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"),
            "error": {"code": code, "message": message, "data": data}}

def jsonrpc_result(msg: dict, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}


# ── Backend presets ──────────────────────────────────────
SAAS_DEFAULT_URL = "https://api.tolls.cargoffer.com"
ENGINE_DEFAULT_URL = "https://tolls-calculator-release.transcend.cargoffer.com"

# MCP friendly vehicle types -> engine vehicleType
VEHICLE_MAP = {
    "light": "ligero",
    "motorcycle": "ligero",
    "truck": "pesado_1",
    "bus": "pesado_2",
    # native values pass through
    "ligero": "ligero",
    "pesado_1": "pesado_1",
    "pesado_2": "pesado_2",
}


# ── Toll MCP Server Logic ───────────────────────────────
class TollsMCPServer:
    def __init__(self, api_url: str, api_key: Optional[str] = None, backend: str = "saas"):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.backend = backend  # 'saas' | 'engine'
        self._client = httpx.Client(timeout=60.0)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            if self.backend == "engine":
                h["x-api-key"] = self.api_key
            else:
                h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _calc_path(self) -> str:
        return "/api/tolls/calculate"

    def _operators_path(self) -> str:
        return "/api/tolls/operators" if self.backend == "engine" else "/api/operators"

    def _tariffs_path(self) -> str:
        return "/api/tolls/tariffs" if self.backend == "engine" else "/api/tariffs"

    def _health_path(self) -> str:
        return "/api/status" if self.backend == "saas" else "/health"

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
                        "vehicle_type": {
                            "type": "string",
                            "enum": ["light", "truck", "bus", "motorcycle", "ligero", "pesado_1", "pesado_2"],
                            "description": "Vehicle type (friendly or native)",
                            "default": "truck",
                        },
                    },
                    "required": ["origin_lat", "origin_lng", "destination_lat", "destination_lng"],
                },
            },
            {
                "name": "list_operators",
                "description": "List toll operators, optionally filtered by country (ES, PT, FR).",
                "inputSchema": {
                    "type": "object",
                    "properties": {"country": {"type": "string", "enum": ["ES", "PT", "FR"]}},
                },
            },
            {
                "name": "get_tariffs",
                "description": "Get toll tariffs, optionally filtered by operator name and/or vehicle type.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operator": {"type": "string"},
                        "vehicle_type": {"type": "string"},
                    },
                },
            },
            {
                "name": "health_check",
                "description": "Check if the tolls API/backend service is healthy.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def _handle_tool_call(self, name: str, args: dict) -> Any:
        if name == "calculate_toll":
            vt = VEHICLE_MAP.get(str(args.get("vehicle_type", "truck")).lower(), "pesado_1")
            body = {
                "origin": {"lat": args["origin_lat"], "lng": args["origin_lng"]},
                "destination": {"lat": args["destination_lat"], "lng": args["destination_lng"]},
                "vehicleType": vt,
            }
            return self._post(self._calc_path(), body)
        elif name == "list_operators":
            params = {}
            if args.get("country"):
                params["country"] = args["country"]
            return self._get(self._operators_path(), params)
        elif name == "get_tariffs":
            params = {}
            if args.get("operator"):
                params["operator"] = args["operator"]
            if args.get("vehicle_type"):
                params["vehicle_type"] = str(args["vehicle_type"]).lower()
            return self._get(self._tariffs_path(), params)
        elif name == "health_check":
            return self._get(self._health_path())
        raise ValueError(f"Unknown tool: {name}")

    def handle_message(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return jsonrpc_result(msg, {
                "protocolVersion": "0.1.0",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "tolls-mcp", "version": "2.0.0"},
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
            return Response(status_code=202)
        return JSONResponse(response)

    async def handle_get(request):
        return JSONResponse({
            "name": "tolls-mcp",
            "version": "2.0.0",
            "backend": server.backend,
            "api_url": server.api_url,
            "transport": "http",
            "protocol": "MCP 0.1.0 (JSON-RPC over HTTP POST /mcp)",
        })

    async def health(request):
        return JSONResponse({
            "status": "ok", "server": "tolls-mcp", "version": "2.0.0",
            "backend": server.backend, "api_url": server.api_url,
        })

    app = Starlette(debug=False, routes=[
        Route("/mcp", handle_request, methods=["POST"]),
        Route("/", handle_get, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
    ])
    uvicorn.run(app, host=host, port=port, log_level="info")


# ── Main ─────────────────────────────────────────────────
def main():
    backend = os.environ.get("TOLLS_BACKEND", "saas").lower()
    if backend not in ("saas", "engine"):
        backend = "saas"
    default_url = SAAS_DEFAULT_URL if backend == "saas" else ENGINE_DEFAULT_URL

    parser = argparse.ArgumentParser(description="Tolls MCP Server")
    parser.add_argument("--backend", default=backend, choices=["saas", "engine"])
    parser.add_argument("--api-url", default=os.environ.get("TOLLS_API_URL", default_url))
    parser.add_argument("--api-key", default=os.environ.get("TOLLS_API_KEY", ""))
    parser.add_argument("--http", action="store_true", default=False, help="Run as HTTP SSE server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")), help="HTTP port")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="HTTP bind host")
    args = parser.parse_args()

    server = TollsMCPServer(api_url=args.api_url, api_key=args.api_key or None, backend=args.backend)

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


if __name__ == "__main__":
    main()
