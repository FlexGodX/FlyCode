"""HTTP boundary fixtures for local model integration tests."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest


@pytest.fixture
def model_server():
    state = SimpleNamespace(requests=[], models=["reasoner", "decider"])
    state.respond = lambda request: completion("[]")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"object": "list", "data": [
                {"id": name, "object": "model", "owned_by": "local", "created": 0}
                for name in state.models
            ]}
            self.reply(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(request)
            response = state.respond(request)
            self.reply(response)

        def reply(self, response):
            status, body = response if isinstance(response, tuple) else (200, response)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    state.completion = completion
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def completion(content, top_logprobs=None):
    logprobs = None if top_logprobs is None else {"content": [{
        "token": " A", "logprob": -0.1, "bytes": [32, 65],
        "top_logprobs": [{"token": token, "logprob": value, "bytes": list(token.encode())}
                         for token, value in top_logprobs],
    }]}
    return {"id": "local-test", "object": "chat.completion", "created": 0, "model": "local",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop", "logprobs": logprobs}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
