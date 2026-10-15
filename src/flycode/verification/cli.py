"""CLI adapter for the registered local pytest verification profile."""

import json
import signal
import sys
from pathlib import Path
from threading import Event, current_thread, main_thread

from .engine import VerificationEngine


def verify_command(args) -> int:
    stopped = Event()
    previous = None
    if current_thread() is main_thread():
        previous = signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        verdict = VerificationEngine(Path(args.output).expanduser()).verify(
            Path(args.repo).expanduser(), timeout=args.timeout, trust=args.trust,
            reruns=args.reruns, should_stop=stopped.is_set)
        print(json.dumps(verdict.model_dump(mode='json'), ensure_ascii=False, indent=2))
        return 130 if verdict.status == 'cancelled' else 0 if verdict.status == 'passed' else 1
    except (OSError, ValueError) as exc:
        print(f'Verification setup failed: {exc}', file=sys.stderr)
        return 2
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
