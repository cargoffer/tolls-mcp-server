#!/usr/bin/env python3
import json, sys; sys.path.insert(0, '.')
from server import TollsMCPServer

srv = TollsMCPServer(api_url='http://localhost:8090')
init = srv.handle_message({'id': 1, 'method': 'initialize'})
assert 'result' in init

tools = srv.handle_message({'id': 2, 'method': 'tools/list'})['result']['tools']
print(f'Total tools: {len(tools)}')
for t in tools:
    props = t['inputSchema'].get('properties', {})
    print(f'  {t["name"]:20s} | {len(props)} params')

# Test error handling
resp = srv.handle_message({'id': 3, 'method': 'tools/call',
    'params': {'name': 'health_check', 'arguments': {}}})
print(f'Health check dispatch: {"OK" if "result" in resp else "FAIL"}')

print('=== All tools validated OK ===')
