"""
Simple API server to serve evolution data to the dashboard.
This can be integrated into the existing dashboard or run as a separate service.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, HTTPServer
import socketserver

# Add the agent directory to path so we can import evolution_logger
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../agent'))
from evolution_logger import EvolutionLogger


class EvolutionAPIHandler(SimpleHTTPRequestHandler):
    """Simple HTTP handler for serving evolution data."""
    
    def do_GET(self):
        """Handle GET requests for evolution data."""
        if self.path == '/api/evolution':
            try:
                evolution_logger = EvolutionLogger()
                timeline = evolution_logger.get_evolution_timeline(30)
                current_focus = evolution_logger.get_current_focus()
                capabilities = evolution_logger.get_capability_metrics()
                summary = evolution_logger.get_evolution_summary(7)
                
                data = {
                    'current_focus': current_focus,
                    'events': timeline,
                    'metrics': capabilities,
                    'summary': summary
                }
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(data, indent=2).encode())
                
            except Exception as e:
                self.send_error(500, f"Error retrieving evolution data: {e}")
        else:
            # Serve static files for other paths
            super().do_GET()


def serve_evolution_api(port=8080):
    """Start the evolution API server."""
    with socketserver.TCPServer(("", port), EvolutionAPIHandler) as httpd:
        print(f"Serving evolution API on port {port}")
        print(f"Visit http://localhost:{port}/api/evolution for data")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")
            httpd.shutdown()


if __name__ == "__main__":
    serve_evolution_api()