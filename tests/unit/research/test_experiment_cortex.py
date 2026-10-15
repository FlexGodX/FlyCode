"""An experiment must keep the endpoint selected when its configuration was read."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


def test_local_endpoint_is_frozen_before_environment_changes(monkeypatch):
    from flycode.research.cortex import CortexConfig

    class Models(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'object': 'list', 'data': [
                {'id': name, 'object': 'model', 'owned_by': 'organization_owner'}
                for name in ['qwen/qwen3.5-9b', 'jev-style-qwen3.5-2b-decision']]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Models)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f'http://127.0.0.1:{server.server_port}/v1'
        monkeypatch.setenv('LMSTUDIO_BASE_URL', endpoint)
        config = CortexConfig(cortex='lmstudio', max_model_calls=4, request_timeout=1.)
        monkeypatch.setenv('LMSTUDIO_BASE_URL', 'http://127.0.0.1:0/v1')
        cortex = config.create_cortex()
        assert str(cortex.reasoner._client.base_url).rstrip('/') == endpoint
        assert cortex.max_model_calls == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
