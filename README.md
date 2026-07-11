# Tolls MCP Server

Model Context Protocol server for the [TRANSCEND Tolls Calculator API](https://tolls.transcend.cargoffer.com).

Calculate toll costs, list operators, and get tariff data across Spain, Portugal, and France — all from your LLM assistant.

## Tools

| Tool | Description | 
|------|-------------|
| `calculate_toll` | Calculate toll costs between two coordinates for a vehicle type |
| `list_operators` | List all toll operators with their highways and countries |
| `get_tariffs` | Get toll tariffs for a specific operator and vehicle type |
| `health_check` | Check if the API service is healthy |

## Quick Start

### Requirements
- Python 3.10+
- `httpx`

### Install & Run

```bash
pip install httpx
python server.py
```

### With Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tolls": {
      "command": "python",
      "args": ["/path/to/server.py"],
      "env": {
        "TOLLS_API_URL": "https://tolls.transcend.cargoffer.com"
      }
    }
  }
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOLLS_API_URL` | `https://tolls.transcend.cargoffer.com` | API base URL |
| `TOLLS_API_KEY` | `""` | Optional API key for authenticated endpoints |

## API

The MCP server wraps the TRANSCEND Tolls Calculator REST API.

### calculate_toll

Calculate toll costs between two coordinates.

**Parameters:**
- `origin_lat`, `origin_lng` — Origin coordinates
- `destination_lat`, `destination_lng` — Destination coordinates  
- `vehicle_type` — `light`, `truck`, `bus`, or `motorcycle` (default: `truck`)

**Example response:**
```json
{
  "totalCost": 14.64,
  "currency": "EUR",
  "segments": [
    {
      "highway": "C-16",
      "operator": "autema",
      "cost": 14.64,
      "distance_km": 45.2
    }
  ]
}
```

### list_operators

List toll operators. Optional `country` filter (`ES`, `PT`, `FR`).

### get_tariffs

Get detailed tariffs for an operator. Parameters:
- `operator` — e.g. `autema`, `atlandes`, `brisa`
- `vehicle_type` — Optional filter

## Deployment

### Docker

```bash
docker build -t tolls-mcp .
docker run -e TOLLS_API_URL=https://tolls.transcend.cargoffer.com tolls-mcp
```

### Coolify

1. Create a new service in Coolify
2. Set the Docker image or use the Dockerfile
3. Set env vars in the Coolify dashboard
4. Domain: `mcp.tolls.cargoffer.com`

## License

MIT
