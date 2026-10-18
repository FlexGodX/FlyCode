"""HTTP boundary fixtures for local model integration tests."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def isolated_state_database(tmp_path, monkeypatch):
    monkeypatch.setenv('FLYCODE_STATE_DATABASE', str(tmp_path / 'state.sqlite'))


@pytest.fixture
def model_server():
    state = SimpleNamespace(requests=[], get_requests=[], models=["reasoner", "decider"], contexts=None,
                            native_available=True, instance_ids={}, remaining_ttl_seconds=None)
    state.respond = lambda request: completion("[]")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state.get_requests.append(self.path)
            if self.path == '/api/v1/models':
                if not state.native_available:
                    self.reply((404, {'error': 'Native metadata unavailable'}))
                    return
                body = {'models': []}
                for name in state.models:
                    capacity = 8192 if state.contexts is None else state.contexts.get(name)
                    body['models'].append({'key': name, 'type': 'llm', 'max_context_length': 262144,
                        'loaded_instances': [{'id': state.instance_ids.get(name, name),
                                              'config': {'context_length': capacity},
                                              **({'remaining_ttl_seconds': state.remaining_ttl_seconds}
                                                 if state.remaining_ttl_seconds is not None else {})}] if capacity else []})
                self.reply(body)
                return
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


@pytest.fixture
def coordinator_processes(tmp_path, monkeypatch):
    """Retain actual launcher Popen handles; never signal a discovered PID."""
    import os
    import subprocess

    original = subprocess.Popen
    processes, logs = [], []
    def launch(args, *values, **options):
        tracked = isinstance(args, list) and 'flycode.orchestration.coordinator' in args
        if tracked:
            assert options.get('start_new_session') is True
            assert options.get('stdin') == subprocess.DEVNULL
            assert options.get('stdout') != subprocess.PIPE and options.get('stderr') != subprocess.PIPE
            logs.append(os.fdopen(os.dup(options['stdout'].fileno()), 'rb'))
        process = original(args, *values, **options)
        if tracked:
            processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', launch)
    try:
        yield processes
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        for index, stream in enumerate(logs):
            stream.seek(0)
            (tmp_path / ('launched-coordinator-' + str(index) + '.log')).write_bytes(stream.read())
            stream.close()
