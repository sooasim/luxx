from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os

base_dir = Path(__file__).resolve().parent.parent
os.chdir(base_dir)
server = ThreadingHTTPServer(('127.0.0.1', 8000), SimpleHTTPRequestHandler)
print('Mock server running at http://127.0.0.1:8000/mock_site/sign_in.html')
server.serve_forever()
