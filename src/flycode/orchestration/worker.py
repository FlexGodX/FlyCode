"""Owned local worker. The independent watchdog starts before heavy imports."""

import os
import signal
import socket
import sys
from threading import Event, Thread

from . import wire


def _watch(channel, finished):
    channel.settimeout(5)
    try:
        while not finished.is_set():
            if not channel.recv(1):
                break
    except OSError:
        pass
    if not finished.is_set():
        # This executable is launched in its own session; never signal the parent's group.
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpid(), signal.SIGKILL)
        os._exit(1)


def main():
    rpc, control = socket.socket(fileno=int(sys.argv[1])), socket.socket(fileno=int(sys.argv[2]))
    rpc.set_inheritable(False)
    control.set_inheritable(False)
    finished = Event()
    Thread(target=_watch, args=(control, finished), daemon=True).start()
    try:
        import asyncio
        from pathlib import Path

        from ..agent import run_agent
        from ..brain import make_brain
        from ..domain import CandidateAction, ToolAction
        from ..domain.orchestration import (
            ToolCall,
            WorkerConfig,
            WorkerReply,
            WorkerRequest,
            WorkerToolOutcome,
        )
        from ..llm.lmstudio import ModelRoutingTrace
        from ..tools.filesystem import ToolResult
        from .environment import AttemptEnvironment
        from .store import Store, StoreError
        from .tools import CandidateMetadataError

        config = WorkerConfig.model_validate(wire.receive(rpc))
        sequence = 0
        def request(operation, payload=None):
            nonlocal sequence
            sequence += 1
            wire.send(rpc, WorkerRequest(sequence=sequence, operation=operation, payload=payload or {}).model_dump(mode='json'))
            reply = WorkerReply.model_validate(wire.receive(rpc))
            if reply.sequence != sequence or reply.error:
                raise RuntimeError(reply.error or 'Worker reply sequence mismatch')
            return reply.payload

        class RemoteCortex:
            def reset(self):
                self.model_calls, self.last_routing = {}, ModelRoutingTrace()

            def generate_candidates(self, context):
                response = request('candidates', context.model_dump(mode='json'))
                self.model_calls = response['model_calls']
                self.last_routing = ModelRoutingTrace.model_validate(response['routing'])
                return [CandidateAction.model_validate(item) for item in response['candidates']]

            def consolidate(self, context, transcript):
                return f'Local agent observation; {len(transcript)} actions; independent review required.'

        def activity(kind, data):
            action = data.get('action', {})
            request('activity', {'kind': kind, 'step': data.get('step'), 'mode': data.get('mode'),
                                 'tool': action.get('tool')})
            if kind == 'controller' and config.message_recipients:
                step, mode = data.get('step'), data.get('mode')
                request('message', {'recipients': config.message_recipients,
                    'idempotency_key': f'controller:{step}',
                    'content': {'kind': 'progress', 'summary': f'Controller entered {mode} at step {step}.', 'step': step}})

        brain, connection = None, None
        try:
            brain = make_brain(config.brain, seed=config.seed)
            brain.set_learning_mode(False)
            request('ready')
            connection = Store(Path(config.database))
            def send_message(action):
                return ToolResult(**request('message_tool', action.model_dump(mode='json')))
            def read_artifact(action):
                return ToolResult(**request('artifact_tool', action.model_dump(mode='json')))
            def admit_tool(action):
                response = request('tool_begin', action.model_dump(mode='json'))
                if response.get('blocked'):
                    error = response['blocked']
                    kind = CandidateMetadataError if error['metadata'] else StoreError
                    raise kind(error['code'], error['message'])
                return ToolCall.model_validate(response['call']), ToolAction.model_validate(response['action'])
            def finish_tool(call, status, error_class):
                outcome = WorkerToolOutcome(call_id=call.id, status=status, error_class=error_class)
                response = request('tool_finish', outcome.model_dump(mode='json'))
                return ToolCall.model_validate(response['call'])
            environment = AttemptEnvironment(Path(config.repository), connection, config.scope, config.attempt_id, config.fence,
                message_executor=send_message, artifact_executor=read_artifact,
                tool_admitter=admit_tool, tool_finisher=finish_tool)
            result = asyncio.run(run_agent(environment, brain, RemoteCortex(), config.task,
                run_id=config.attempt_id, on_event=activity, should_stop=finished.is_set))
            request('result', result.model_dump(mode='json'))
        except BaseException as error:
            request('error', {'error_class': type(error).__name__, 'code': getattr(error, 'code', None)})
            raise
        finally:
            if brain is not None:
                brain.close()
            if connection is not None:
                connection.close()
    finally:
        finished.set()
        rpc.close()
        control.close()


if __name__ == '__main__':
    main()
