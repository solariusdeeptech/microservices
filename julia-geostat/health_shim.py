#!/usr/bin/env python3
"""Ultra-lightweight health shim for Cloud Run startup probe.
Starts in <1s, responds 200 to /health while Julia loads.
"""
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

PORT = int(os.environ.get('PORT', '8080'))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            body = json.dumps({
                'status': 'warming_up',
                'service': 'julia-geostat',
                'version': '1.0.0',
                'ready': False,
                'message': 'Julia GeoStats is loading, please wait...',
                'timestamp': datetime.utcnow().isoformat()
            })
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            body = json.dumps({
                'error': 'Service is starting up, please retry in 30 seconds',
                'retry_after': 30
            })
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '30')
            self.end_headers()
            self.wfile.write(body.encode())

    def do_POST(self):
        body = json.dumps({
            'error': 'Service is starting up, please retry in 30 seconds',
            'retry_after': 30
        })
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Retry-After', '30')
        self.end_headers()
        self.wfile.write(body.encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key')
        self.end_headers()

    def log_message(self, format, *args):
        # Suppress request logs to keep output clean
        pass

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    print(f'[health-shim] Ready on port {PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
