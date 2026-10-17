"""Fixed legacy tool entry point, launched only by the owned-process adapter."""

import hashlib
import sys
from pathlib import Path

from ..environment.observer import observe
from ..tools import register_environment_tools, run_tool
from ..tools.filesystem import ToolResult
from .execution import ToolReply, ToolRequest


def main():
    path = Path(sys.argv[1])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sys.argv[2]:
        raise ValueError('Tool request changed')
    request = ToolRequest.model_validate_json(raw)
    register_environment_tools()
    if request.action.tool == '__observe__':
        observation = observe(Path(request.root), **request.action.arguments)
        result = ToolResult(success=True, payload={'observation': observation.model_dump(mode='json')})
    else:
        result = run_tool(request.action.tool, Path(request.root), **request.action.arguments)
    reply = ToolReply(**vars(result))
    output = path.with_name('result.tmp')
    output.write_text(reply.model_dump_json())
    output.replace(path.with_name('result.json'))


if __name__ == '__main__':
    main()
