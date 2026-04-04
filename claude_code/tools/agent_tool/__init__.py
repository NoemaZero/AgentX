"""AgentTool package — strict translation of tools/AgentTool/ directory.

Module mapping:
    constants.py    ← constants.ts         (tool names, one-shot types)
    definitions.py  ← loadAgentsDir.ts     (AgentDefinition types, loading)
    built_in.py     ← builtInAgents.ts     (built-in agent registry)
    utils.py        ← agentToolUtils.ts    (tool filtering, finalize, async lifecycle)
    fork.py         ← forkSubagent.ts      (fork agent, buildForkedMessages)
    memory.py       ← agentMemory.ts       (agent memory scopes)
    run_agent.py    ← runAgent.ts          (core run_agent async generator)
    resume.py       ← resumeAgent.ts       (resume agent background)
    prompt.py       ← prompt.ts            (dynamic prompt generation)
    tool.py         ← AgentTool.tsx        (AgentTool class)
"""

from claude_code.tools.agent_tool.tool import AgentTool

__all__ = ["AgentTool"]
