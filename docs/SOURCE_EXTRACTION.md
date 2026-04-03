# Claude Code Source Extraction — Exact Strings for Python Translation

## 1. Tool Name Constants (Exact Strings)

```
BASH_TOOL_NAME          = 'Bash'
FILE_READ_TOOL_NAME     = 'Read'
FILE_WRITE_TOOL_NAME    = 'Write'
FILE_EDIT_TOOL_NAME     = 'Edit'
GLOB_TOOL_NAME          = 'Glob'
GREP_TOOL_NAME          = 'Grep'
AGENT_TOOL_NAME         = 'Agent'
LEGACY_AGENT_TOOL_NAME  = 'Task'
TASK_OUTPUT_TOOL_NAME   = 'TaskOutput'
TASK_STOP_TOOL_NAME     = 'TaskStop'
TASK_CREATE_TOOL_NAME   = 'TaskCreate'
TASK_GET_TOOL_NAME      = 'TaskGet'
TASK_UPDATE_TOOL_NAME   = 'TaskUpdate'
TASK_LIST_TOOL_NAME     = 'TaskList'
WEB_FETCH_TOOL_NAME     = 'WebFetch'
WEB_SEARCH_TOOL_NAME    = 'WebSearch'
TODO_WRITE_TOOL_NAME    = 'TodoWrite'
NOTEBOOK_EDIT_TOOL_NAME = 'NotebookEdit'
ASK_USER_QUESTION_TOOL_NAME = 'AskUserQuestion'
SKILL_TOOL_NAME         = 'Skill'
ENTER_PLAN_MODE_TOOL_NAME   = 'EnterPlanMode'
EXIT_PLAN_MODE_TOOL_NAME    = 'ExitPlanMode'   # (also EXIT_PLAN_MODE_V2_TOOL_NAME)
TOOL_SEARCH_TOOL_NAME   = 'ToolSearch'
SEND_MESSAGE_TOOL_NAME  = 'SendMessage'
CONFIG_TOOL_NAME        = 'Config'
ENTER_WORKTREE_TOOL_NAME = 'EnterWorktree'
EXIT_WORKTREE_TOOL_NAME  = 'ExitWorktree'
LSP_TOOL_NAME           = 'LSP'
LIST_MCP_RESOURCES_TOOL_NAME = 'ListMcpResourcesTool'
READ_MCP_RESOURCE_TOOL_NAME  = 'ReadMcpResourceTool'
SYNTHETIC_OUTPUT_TOOL_NAME   = 'StructuredOutput'
TEAM_CREATE_TOOL_NAME   = 'TeamCreate'
TEAM_DELETE_TOOL_NAME   = 'TeamDelete'
BRIEF_TOOL_NAME         = 'Brief'       # (from BriefTool/prompt.ts)
SLEEP_TOOL_NAME         = 'Sleep'       # (from SleepTool/prompt.ts)
```

---

## 2. Tool Definitions — Exact Input Schemas, Descriptions, ReadOnly, ConcurrencySafe

### 2.1 BashTool
- **name**: `'Bash'`
- **description** (from `prompt.ts:getSimplePrompt()`): `'Executes a given bash command and returns its output.'` (first line; full prompt is dynamic)
- **isReadOnly**: varies by command (has `readOnlyValidation.ts`)
- **isConcurrencySafe**: `false` (default)
- **shouldDefer**: `false`
- **inputSchema** (Zod → JSON):
```typescript
z.strictObject({
  command: z.string().describe('The command to execute'),
  timeout: semanticNumber(z.number().optional()).describe(`Optional timeout in milliseconds (max ${getMaxTimeoutMs()})`),
  description: z.string().optional().describe(`Clear, concise description of what this command does in active voice. Never use words like "complex" or "risk" in the description - just describe what it does.

For simple commands (git, npm, standard CLI tools), keep it brief (5-10 words):
- ls → "List files in current directory"
- git status → "Show working tree status"
- npm install → "Install package dependencies"

For commands that are harder to parse at a glance (piped commands, obscure flags, etc.), add enough context to clarify what it does:
- find . -name "*.tmp" -exec rm {} \\; → "Find and delete all .tmp files recursively"
- git reset --hard origin/main → "Discard all local changes and match remote main"
- curl -s url | jq '.data[]' → "Fetch JSON from URL and extract data array elements"`),
  run_in_background: semanticBoolean(z.boolean().optional()).describe(`Set to true to run this command in the background. Use Read to read the output later.`),
  dangerouslyDisableSandbox: semanticBoolean(z.boolean().optional()).describe('Set this to true to dangerously override sandbox mode and run commands without sandboxing.'),
})
```
Note: `_simulatedSedEdit` always omitted from model-facing schema. `run_in_background` omitted when `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`.

### 2.2 FileReadTool (Read)
- **name**: `'Read'`
- **description**: `'Read a file from the local filesystem.'`
- **isReadOnly**: `true` (defined inline)
- **isConcurrencySafe**: `true` (defined inline)
- **shouldDefer**: `false`
- **inputSchema**: defined in `FileReadTool.ts` — uses `lazySchema` (reads dynamically). Key parameters:
  - `file_path`: string, "Absolute path to the file to read"
  - `offset`: number, optional
  - `limit`: number, optional  
  - `pages`: string, optional (for PDFs)

### 2.3 FileWriteTool (Write)
- **name**: `'Write'`
- **description**: `'Write a file to the local filesystem.'`
- **isReadOnly**: `false` (default)
- **isConcurrencySafe**: `false` (default)
- **shouldDefer**: `false`
- **strict**: `true`
- **searchHint**: `'create or overwrite files'`
- **inputSchema**:
```typescript
z.strictObject({
  file_path: z.string().describe('The absolute path to the file to write (must be absolute, not relative)'),
  content: z.string().describe('The content to write to the file'),
})
```

### 2.4 FileEditTool (Edit)
- **name**: `'Edit'`
- **description**: `'A tool for editing files'`
- **isReadOnly**: `false` (default)
- **isConcurrencySafe**: `false` (default)
- **shouldDefer**: `false`
- **strict**: `true`
- **searchHint**: `'modify file contents in place'`
- **inputSchema**:
```typescript
z.strictObject({
  file_path: z.string().describe('The absolute path to the file to modify'),
  old_string: z.string().describe('The text to replace'),
  new_string: z.string().describe('The text to replace it with (must be different from old_string)'),
  replace_all: semanticBoolean(z.boolean().default(false).optional()).describe('Replace all occurrences of old_string (default false)'),
})
```

### 2.5 GlobTool
- **name**: `'Glob'`
- **description**:
```
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead
```
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `false`
- **searchHint**: `'find files by name pattern or wildcard'`
- **inputSchema**:
```typescript
z.strictObject({
  pattern: z.string().describe('The glob pattern to match files against'),
  path: z.string().optional().describe(
    'The directory to search in. If not specified, the current working directory will be used. IMPORTANT: Omit this field to use the default directory. DO NOT enter "undefined" or "null" - simply omit it for the default behavior. Must be a valid directory path if provided.',
  ),
})
```

### 2.6 GrepTool
- **name**: `'Grep'`
- **description** (dynamic, from `getDescription()`): `'A powerful search tool built on ripgrep'` + usage instructions
- **isReadOnly**: `true` (default → not overridden, relies on return value)
- **isConcurrencySafe**: `true` (defined inline)
- **shouldDefer**: `false`
- **searchHint**: not found (no searchHint on GrepTool)
- **inputSchema**:
```typescript
z.strictObject({
  pattern: z.string().describe('The regular expression pattern to search for in file contents'),
  path: z.string().optional().describe('File or directory to search in (rg PATH). Defaults to current working directory.'),
  glob: z.string().optional().describe('Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}") - maps to rg --glob'),
  output_mode: z.enum(['content', 'files_with_matches', 'count']).optional().describe(
    'Output mode: "content" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), "files_with_matches" shows file paths (supports head_limit), "count" shows match counts (supports head_limit). Defaults to "files_with_matches".',
  ),
  '-B': semanticNumber(z.number().optional()).describe('Number of lines to show before each match (rg -B). Requires output_mode: "content", ignored otherwise.'),
  '-A': semanticNumber(z.number().optional()).describe('Number of lines to show after each match (rg -A). Requires output_mode: "content", ignored otherwise.'),
  '-C': semanticNumber(z.number().optional()).describe('Alias for context.'),
  context: semanticNumber(z.number().optional()).describe('Number of lines to show before and after each match (rg -C). Requires output_mode: "content", ignored otherwise.'),
  '-n': semanticBoolean(z.boolean().optional()).describe('Show line numbers in output (rg -n). Requires output_mode: "content", ignored otherwise. Defaults to true.'),
  '-i': semanticBoolean(z.boolean().optional()).describe('Case insensitive search (rg -i)'),
  type: z.string().optional().describe('File type to search (rg --type). Common types: js, py, rust, go, java, etc. More efficient than include for standard file types.'),
  head_limit: semanticNumber(z.number().optional()).describe('Limit output to first N lines/entries, equivalent to "| head -N". Works across all output modes: content (limits output lines), files_with_matches (limits file paths), count (limits count entries). Defaults to 250 when unspecified. Pass 0 for unlimited (use sparingly — large result sets waste context).'),
  offset: semanticNumber(z.number().optional()).describe('Skip first N lines/entries before applying head_limit, equivalent to "| tail -n +N | head -N". Works across all output modes. Defaults to 0.'),
  multiline: semanticBoolean(z.boolean().optional()).describe('Enable multiline mode where . matches newlines and patterns can span lines (rg -U --multiline-dotall). Default: false.'),
})
```

### 2.7 AgentTool
- **name**: `'Agent'`
- **aliases**: `['Task']` (from LEGACY_AGENT_TOOL_NAME)
- **isReadOnly**: `false` (default)
- **isConcurrencySafe**: `false` (default)
- **shouldDefer**: depends on `isForkSubagentEnabled()`
- **inputSchema** (base — full schema adds isolation, cwd, etc.):
```typescript
z.object({
  description: z.string().describe('A short (3-5 word) description of the task'),
  prompt: z.string().describe('The task for the agent to perform'),
  subagent_type: z.string().optional().describe('The type of specialized agent to use for this task'),
  model: z.enum(['sonnet', 'opus', 'haiku']).optional().describe("Optional model override for this agent. Takes precedence over the agent definition's model frontmatter. If omitted, uses the agent definition's model, or inherits from the parent."),
  run_in_background: z.boolean().optional().describe('Set to true to run this agent in the background. You will be notified when it completes.')
})
```

### 2.8 WebFetchTool
- **name**: `'WebFetch'`
- **userFacingName**: `'Fetch'`
- **description** (dynamic): `'Claude wants to fetch content from ${hostname}'`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **searchHint**: `'fetch and extract content from a URL'`
- **inputSchema**:
```typescript
z.strictObject({
  url: z.string().url().describe('The URL to fetch content from'),
  prompt: z.string().describe('The prompt to run on the fetched content'),
})
```

### 2.9 WebSearchTool
- **name**: `'WebSearch'`
- **isReadOnly**: `true` (check)
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true` (check)
- **inputSchema**:
```typescript
z.strictObject({
  query: z.string().min(2).describe('The search query to use'),
  allowed_domains: z.array(z.string()).optional().describe('Only include search results from these domains'),
  blocked_domains: z.array(z.string()).optional().describe('Never include search results from these domains'),
})
```

### 2.10 TodoWriteTool
- **name**: `'TodoWrite'`
- **description**: `'Use this tool to create and manage a todo list for tracking progress on tasks.'` (from DESCRIPTION constant)
- **isReadOnly**: `false` (default, modifies state)
- **isConcurrencySafe**: `false` (default)
- **shouldDefer**: `true`
- **strict**: `true`
- **searchHint**: `'manage the session task checklist'`
- **inputSchema**:
```typescript
z.strictObject({
  todos: TodoListSchema().describe('The updated todo list'),
})
```

### 2.11 NotebookEditTool
- **name**: `'NotebookEdit'`
- **description**: `'Replace the contents of a specific cell in a Jupyter notebook.'`
- **shouldDefer**: `true`
- **searchHint**: `'edit Jupyter notebook cells (.ipynb)'`
- **inputSchema**:
```typescript
z.strictObject({
  notebook_path: z.string().describe('The absolute path to the Jupyter notebook file to edit (must be absolute, not relative)'),
  cell_id: z.string().optional().describe('The ID of the cell to edit. When inserting a new cell, the new cell will be inserted after the cell with this ID, or at the beginning if not specified.'),
  new_source: z.string().describe('The new source for the cell'),
  cell_type: z.enum(['code', 'markdown']).optional().describe('The type of the cell (code or markdown). If not specified, it defaults to the current cell type. If using edit_mode=insert, this is required.'),
  edit_mode: z.enum(['replace', 'insert', 'delete']).optional().describe('The type of edit to make (replace, insert, delete). Defaults to replace.'),
})
```

### 2.12 AskUserQuestionTool
- **name**: `'AskUserQuestion'`
- **description**: `'Asks the user multiple choice questions to gather information, clarify ambiguity, understand preferences, make decisions or offer them choices.'`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **searchHint**: `'prompt the user with a multiple-choice question'`
- **inputSchema**:
```typescript
z.strictObject({
  questions: z.array(questionSchema()).min(1).max(4).describe('Questions to ask the user (1-4 questions)'),
  answers: z.record(z.string(), z.string()).optional().describe('User answers collected by the permission component'),
  annotations: annotationsSchema(),
  metadata: z.object({
    source: z.string().optional().describe('Optional identifier for the source...')
  }).optional()
}).refine(UNIQUENESS_REFINE.check, { message: UNIQUENESS_REFINE.message })
```
where `questionSchema`:
```typescript
z.object({
  question: z.string().describe('The complete question to ask the user...'),
  header: z.string().describe(`Very short label displayed as a chip/tag (max ${ASK_USER_QUESTION_TOOL_CHIP_WIDTH} chars)...`),
  options: z.array(questionOptionSchema()).min(2).max(4).describe('The available choices...'),
  multiSelect: z.boolean().default(false).describe('Set to true to allow the user to select multiple options...')
})
```

### 2.13 SkillTool
- **name**: `'Skill'`
- **shouldDefer**: `false`

### 2.14 TaskOutputTool
- **name**: `'TaskOutput'`
- **inputSchema**:
```typescript
z.strictObject({
  task_id: z.string().describe('The task ID to get output from'),
  block: semanticBoolean(z.boolean().default(true)).describe('Whether to wait for completion'),
  timeout: z.number().min(0).max(600000).default(30000).describe('Max wait time in ms')
})
```

### 2.15 TaskStopTool
- **name**: `'TaskStop'`
- **aliases**: `['KillShell']`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **searchHint**: `'kill a running background task'`
- **inputSchema**:
```typescript
z.strictObject({
  task_id: z.string().optional().describe('The ID of the background task to stop'),
  shell_id: z.string().optional().describe('Deprecated: use task_id instead'),
})
```

### 2.16 EnterPlanModeTool
- **name**: `'EnterPlanMode'`
- **description**: `'Requests permission to enter plan mode for complex tasks requiring exploration and design'`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **searchHint**: `'switch to plan mode to design an approach before coding'`
- **inputSchema**: `z.strictObject({})` (empty)

### 2.17 ExitPlanModeV2Tool
- **name**: `'ExitPlanMode'`
- **inputSchema**:
```typescript
z.strictObject({
  allowedPrompts: z.array(allowedPromptSchema()).optional().describe('Prompt-based permissions needed to implement the plan...'),
}).passthrough()
```

### 2.18 ToolSearchTool
- **name**: `'ToolSearch'`
- **inputSchema**:
```typescript
z.object({
  query: z.string().describe('Query to find deferred tools. Use "select:<tool_name>" for direct selection, or keywords to search.'),
  max_results: z.number().optional().default(5).describe('Maximum number of results to return (default: 5)'),
})
```

### 2.19 SendMessageTool
- **name**: `'SendMessage'`

### 2.20 ListMcpResourcesTool
- **name**: `'ListMcpResourcesTool'`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **inputSchema**:
```typescript
z.object({
  server: z.string().optional().describe('Optional server name to filter resources by'),
})
```

### 2.21 ReadMcpResourceTool
- **name**: `'ReadMcpResourceTool'`
- **isReadOnly**: `true`
- **isConcurrencySafe**: `true`
- **shouldDefer**: `true`
- **inputSchema**:
```typescript
z.object({
  server: z.string().describe('The MCP server name'),
  uri: z.string().describe('The resource URI to read'),
})
```

### 2.22 BriefTool
- **name**: `'Brief'` (from prompt.ts)
- **inputSchema**:
```typescript
z.strictObject({
  message: z.string().describe('The message for the user. Supports markdown formatting.'),
  attachments: z.array(z.string()).optional().describe('Optional file paths (absolute or relative to cwd) to attach...'),
  status: z.enum(['normal', 'proactive']).describe("Use 'proactive' when you're surfacing something the user hasn't asked for..."),
})
```

---

## 3. Tool Registry — getAllBaseTools() Order

From `tools.ts:getAllBaseTools()`:
```
1.  AgentTool
2.  TaskOutputTool
3.  BashTool
4.  GlobTool (if !hasEmbeddedSearchTools)
5.  GrepTool (if !hasEmbeddedSearchTools)
6.  ExitPlanModeV2Tool
7.  FileReadTool
8.  FileEditTool
9.  FileWriteTool
10. NotebookEditTool
11. WebFetchTool
12. TodoWriteTool
13. WebSearchTool
14. TaskStopTool
15. AskUserQuestionTool
16. SkillTool
17. EnterPlanModeTool
18. ConfigTool (ant-only)
19. TungstenTool (ant-only)
20. TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool (if isTodoV2Enabled)
21. SendMessageTool
22. BriefTool
23. ListMcpResourcesTool
24. ReadMcpResourceTool
25. ToolSearchTool (if isToolSearchEnabledOptimistic)
```

### Disallowed Tools Sets:
```typescript
ALL_AGENT_DISALLOWED_TOOLS = new Set([
  'TaskOutput', 'ExitPlanMode', 'EnterPlanMode', 'AskUserQuestion', 'TaskStop'
  // Agent only disallowed for non-ant
])

ASYNC_AGENT_ALLOWED_TOOLS = new Set([
  'Read', 'WebSearch', 'TodoWrite', 'Grep', 'WebFetch', 'Glob',
  ...SHELL_TOOL_NAMES /* Bash, PowerShell */,
  'Edit', 'Write', 'NotebookEdit', 'Skill', 'StructuredOutput',
  'ToolSearch', 'EnterWorktree', 'ExitWorktree'
])

COORDINATOR_MODE_ALLOWED_TOOLS = new Set([
  'Agent', 'TaskStop', 'SendMessage', 'StructuredOutput'
])
```

---

## 4. Tool.ts Interface (Complete)

### Key Types:
```typescript
export type ToolInputJSONSchema = {
  [x: string]: unknown
  type: 'object'
  properties?: { [x: string]: unknown }
}

export type ValidationResult =
  | { result: true }
  | { result: false; message: string; errorCode: number }

export type ToolResult<T> = {
  data: T
  newMessages?: (UserMessage | AssistantMessage | AttachmentMessage | SystemMessage)[]
  contextModifier?: (context: ToolUseContext) => ToolUseContext
  mcpMeta?: { _meta?: Record<string, unknown>; structuredContent?: Record<string, unknown> }
}

// The Tool interface (key methods):
export type Tool<Input, Output, P> = {
  aliases?: string[]
  searchHint?: string
  readonly name: string
  readonly inputSchema: Input
  readonly inputJSONSchema?: ToolInputJSONSchema
  readonly shouldDefer?: boolean
  readonly alwaysLoad?: boolean
  readonly strict?: boolean
  maxResultSizeChars: number
  mcpInfo?: { serverName: string; toolName: string }
  isMcp?: boolean
  isLsp?: boolean

  call(args, context, canUseTool, parentMessage, onProgress?): Promise<ToolResult<Output>>
  description(input, options): Promise<string>
  prompt(options): Promise<string>
  isEnabled(): boolean
  isReadOnly(input): boolean
  isDestructive?(input): boolean
  isConcurrencySafe(input): boolean
  checkPermissions(input, context): Promise<PermissionResult>
  validateInput?(input, context): Promise<ValidationResult>
  userFacingName(input): string
  toAutoClassifierInput(input): unknown
  mapToolResultToToolResultBlockParam(content, toolUseID): ToolResultBlockParam
  getPath?(input): string
  preparePermissionMatcher?(input): Promise<(pattern: string) => boolean>
  backfillObservableInput?(input: Record<string, unknown>): void
  interruptBehavior?(): 'cancel' | 'block'
  isSearchOrReadCommand?(input): { isSearch: boolean; isRead: boolean; isList?: boolean }
  isOpenWorld?(input): boolean
  requiresUserInteraction?(): boolean
}

// Tools is a readonly array:
export type Tools = readonly Tool[]
```

### buildTool defaults:
```typescript
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input?) => false,
  isReadOnly: (_input?) => false,
  isDestructive: (_input?) => false,
  checkPermissions: (input, _ctx?) => Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: (_input?) => '',
  userFacingName: (_input?) => '',
}
```

---

## 5. Types Directory

Files:
```
types/command.ts      — Command, PromptCommand, LocalCommand types
types/generated/      — (protobuf/events types)
types/hooks.ts        — HookEvent, syncHookResponseSchema, PromptRequest, PromptResponse
types/ids.ts          — SessionId, AgentId (branded string types)
types/logs.ts         — LogOption type
types/permissions.ts  — PermissionMode, PermissionBehavior, PermissionRule, PermissionUpdate
types/plugin.ts       — PluginManifest
types/textInputTypes.ts — OrphanedPermission type
```

### Key Permission Types:
```typescript
export type PermissionMode = 'acceptEdits' | 'bypassPermissions' | 'default' | 'dontAsk' | 'plan' | 'auto' | 'bubble'
export type PermissionBehavior = 'allow' | 'deny' | 'ask'
export type PermissionRuleSource = 'userSettings' | 'projectSettings' | 'localSettings' | 'flagSettings' | 'policySettings' | 'cliArg' | 'command' | 'session'

export type PermissionRuleValue = {
  toolName: string
  ruleContent?: string
}

export type PermissionRule = {
  source: PermissionRuleSource
  ruleBehavior: PermissionBehavior
  ruleValue: PermissionRuleValue
}
```

### Branded ID Types:
```typescript
export type SessionId = string & { readonly __brand: 'SessionId' }
export type AgentId = string & { readonly __brand: 'AgentId' }
```

---

## 6. System Prompt Structure

### Main Entry: `getSystemPrompt(tools, model, additionalWorkingDirectories?, mcpClients?)`

Returns `string[]` — an array of sections joined later. Static sections first, then dynamic boundary marker, then dynamic sections.

### Static Sections (in order):
1. `getSimpleIntroSection(outputStyleConfig)` — identity + cyber risk
2. `getSimpleSystemSection()` — system behavior rules
3. `getSimpleDoingTasksSection()` — coding style rules
4. `getActionsSection()` — "Executing actions with care"
5. `getUsingYourToolsSection(enabledTools)` — tool preference rules
6. `getSimpleToneAndStyleSection()` — tone guidance
7. `getOutputEfficiencySection()` — conciseness rules
8. `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` marker

### Dynamic Sections (post-boundary, registry-managed):
- `session_guidance` — session-specific guidance
- `memory` — CLAUDE.md/memory prompt
- `ant_model_override` — ant-only overrides
- `env_info_simple` — environment info
- `language` — language preference
- `output_style` — output style config
- `mcp_instructions` — MCP server instructions
- `scratchpad` — scratchpad directory instructions
- `frc` — function result clearing
- `summarize_tool_results`
- `numeric_length_anchors` (ant-only)
- `token_budget` (feature gated)
- `brief` (feature gated)

### Key Prompt Constants:
```typescript
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = '__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__'
FRONTIER_MODEL_NAME = 'Claude Opus 4.6'
CLAUDE_4_5_OR_4_6_MODEL_IDS = {
  opus: 'claude-opus-4-6',
  sonnet: 'claude-sonnet-4-6',
  haiku: 'claude-haiku-4-5-20251001',
}
DEFAULT_AGENT_PROMPT = `You are an agent for Claude Code, Anthropic's official CLI for Claude. Given the user's message, you should use the tools available to complete the task. Complete the task fully—don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings — the caller will relay this to the user, so it only needs the essentials.`
```

### Intro Section (verbatim):
```
You are an interactive agent that helps users with software engineering tasks. Use the instructions below and the tools available to you to assist the user.

IMPORTANT: Refuse to write code or provide assistance for activities that could cause harm... [CYBER_RISK_INSTRUCTION]
IMPORTANT: You must NEVER generate or guess URLs for the user unless you are confident that the URLs are for helping the user with programming. You may use URLs provided by the user in their messages or local files.
```

---

## 7. Query Loop Structure

### query.ts exports:
```typescript
export type QueryParams = {
  messages: Message[]
  systemPrompt: SystemPrompt
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  canUseTool: CanUseToolFn
  toolUseContext: ToolUseContext
  fallbackModel?: string
  querySource: QuerySource
  maxOutputTokensOverride?: number
  maxTurns?: number
  skipCacheWrite?: boolean
  taskBudget?: { total: number }
  deps?: QueryDeps
}

export async function* query(params: QueryParams): AsyncGenerator<
  StreamEvent | RequestStartEvent | Message | TombstoneMessage | ToolUseSummaryMessage,
  Terminal  // { reason: string }
>
```

The `query()` function:
1. Delegates to `queryLoop()` — an internal generator
2. Tracks consumed command UUIDs
3. Notifies command lifecycle on completion

`queryLoop()` structure:
- Holds immutable params (systemPrompt, userContext, systemContext, canUseTool, fallbackModel, querySource, maxTurns)
- Maintains mutable `State` across iterations:
  ```typescript
  type State = {
    messages: Message[]
    toolUseContext: ToolUseContext
    autoCompactTracking: AutoCompactTrackingState | undefined
    maxOutputTokensRecoveryCount: number
    hasAttemptedReactiveCompact: boolean
    turnCount: number
    pendingToolUseSummary: ToolUseSummaryMessage | undefined
    maxOutputTokensOverride: number | undefined
    stopHookActive: boolean | undefined
    transition: Continue | undefined
  }
  ```
- Main `while(true)` loop:
  - Destructures state
  - Starts skill discovery prefetch
  - Yields `stream_request_start`
  - Initializes query chain tracking
  - Calls streaming API
  - Runs tool orchestration (`runTools`)
  - Applies tool result budget
  - Handles auto-compact
  - Recurses on tool results (increments turnCount)
  - Checks maxTurns limit

### MAX_OUTPUT_TOKENS_RECOVERY_LIMIT: 3

---

## 8. QueryEngine Class

```typescript
export type QueryEngineConfig = {
  cwd: string
  tools: Tools
  commands: Command[]
  mcpClients: MCPServerConnection[]
  agents: AgentDefinition[]
  canUseTool: CanUseToolFn
  getAppState: () => AppState
  setAppState: (f: (prev: AppState) => AppState) => void
  initialMessages?: Message[]
  readFileCache: FileStateCache
  customSystemPrompt?: string
  appendSystemPrompt?: string
  userSpecifiedModel?: string
  fallbackModel?: string
  thinkingConfig?: ThinkingConfig
  maxTurns?: number
  maxBudgetUsd?: number
  taskBudget?: { total: number }
  jsonSchema?: Record<string, unknown>
  verbose?: boolean
  replayUserMessages?: boolean
  handleElicitation?: ToolUseContext['handleElicitation']
  includePartialMessages?: boolean
  setSDKStatus?: (status: SDKStatus) => void
  abortController?: AbortController
  orphanedPermission?: OrphanedPermission
  snipReplay?: (yieldedSystemMsg: Message, store: Message[]) => { messages: Message[]; executed: boolean } | undefined
}

export class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]
  private abortController: AbortController
  private permissionDenials: SDKPermissionDenial[]
  private totalUsage: NonNullableUsage
  private hasHandledOrphanedPermission = false
  private readFileState: FileStateCache
  private discoveredSkillNames = new Set<string>()
  private loadedNestedMemoryPaths = new Set<string>()
  
  constructor(config: QueryEngineConfig)
  // ... submitMessage(), etc.
}
```

---

## 9. Commands Structure

### Command Types (from `types/command.ts`):
```typescript
export type Command = (PromptCommand | LocalCommand | LocalJSXCommand) & CommandBase

export type CommandBase = {
  availability?: CommandAvailability[]
  description: string
  isEnabled?: () => boolean
  isHidden?: boolean
  name: string
  aliases?: string[]
  isMcp?: boolean
  argumentHint?: string
  whenToUse?: string
  version?: string
  disableModelInvocation?: boolean
  userInvocable?: boolean
  loadedFrom?: 'commands_DEPRECATED' | 'skills' | 'plugin' | 'managed' | 'bundled' | 'mcp'
  kind?: 'workflow'
  immediate?: boolean
  isSensitive?: boolean
}

export type PromptCommand = {
  type: 'prompt'
  progressMessage: string
  contentLength: number
  argNames?: string[]
  allowedTools?: string[]
  model?: string
  source: SettingSource | 'builtin' | 'mcp' | 'plugin' | 'bundled'
  context?: 'inline' | 'fork'
  agent?: string
  effort?: EffortValue
  paths?: string[]
  getPromptForCommand(args: string, context: ToolUseContext): Promise<ContentBlockParam[]>
}
```

### Built-in Commands (from `commands.ts`):
```
addDir, autofixPr, backfillSessions, btw, goodClaude, issue, feedback,
clear, color, commit, copy, desktop, commitPushPr, compact, config,
context, cost, diff, ctx_viz, doctor, memory, help, ide, init,
initVerifiers, keybindings, login, logout, installGitHubApp,
installSlackApp, breakCache, mcp, mobile, onboarding, pr_comments,
releaseNotes, rename, resume, review, ultrareview, session, share,
skills, status, tasks, teleport, securityReview, bughunter,
terminalSetup, usage, theme, vim, thinkback, thinkbackPlay,
permissions, plan, fast, passes, privacySettings, hooks, files,
branch, agents, plugin, reloadPlugins, rewind, heapDump, mockLimits,
bridgeKick, version, summary, resetLimits, antTrace, perfIssue,
sandboxToggle, chrome, stickers, advisor, env, exit, exportCommand,
model, tag, outputStyle, remoteEnv, upgrade, extraUsage,
rateLimitOptions, statusline, effort, stats, usageReport (insights)
```

---

## 10. ToolUseContext (from Tool.ts)

```typescript
export type ToolUseContext = {
  options: {
    commands: Command[]
    debug: boolean
    mainLoopModel: string
    tools: Tools
    verbose: boolean
    thinkingConfig: ThinkingConfig
    mcpClients: MCPServerConnection[]
    mcpResources: Record<string, ServerResource[]>
    isNonInteractiveSession: boolean
    agentDefinitions: AgentDefinitionsResult
    maxBudgetUsd?: number
    customSystemPrompt?: string
    appendSystemPrompt?: string
    querySource?: QuerySource
    refreshTools?: () => Tools
  }
  abortController: AbortController
  readFileState: FileStateCache
  getAppState(): AppState
  setAppState(f: (prev: AppState) => AppState): void
  setAppStateForTasks?: (f: (prev: AppState) => AppState) => void
  handleElicitation?: (serverName, params, signal) => Promise<ElicitResult>
  setToolJSX?: SetToolJSXFn
  addNotification?: (notif: Notification) => void
  appendSystemMessage?: (msg) => void
  sendOSNotification?: (opts) => void
  nestedMemoryAttachmentTriggers?: Set<string>
  loadedNestedMemoryPaths?: Set<string>
  dynamicSkillDirTriggers?: Set<string>
  discoveredSkillNames?: Set<string>
  userModified?: boolean
  setInProgressToolUseIDs: (f: (prev: Set<string>) => Set<string>) => void
  setHasInterruptibleToolInProgress?: (v: boolean) => void
  setResponseLength: (f: (prev: number) => number) => void
  pushApiMetricsEntry?: (ttftMs: number) => void
  setStreamMode?: (mode: SpinnerMode) => void
  onCompactProgress?: (event: CompactProgressEvent) => void
  setSDKStatus?: (status: SDKStatus) => void
  openMessageSelector?: () => void
  updateFileHistoryState: (updater) => void
  updateAttributionState: (updater) => void
  setConversationId?: (id: UUID) => void
  agentId?: AgentId
  agentType?: string
  requireCanUseTool?: boolean
  messages: Message[]
  fileReadingLimits?: { maxTokens?: number; maxSizeBytes?: number }
  globLimits?: { maxResults?: number }
  toolDecisions?: Map<string, { source: string; decision: 'accept' | 'reject'; timestamp: number }>
  queryTracking?: QueryChainTracking
  requestPrompt?: (sourceName, toolInputSummary?) => (request: PromptRequest) => Promise<PromptResponse>
  toolUseId?: string
  criticalSystemReminder_EXPERIMENTAL?: string
  preserveToolUseResults?: boolean
  localDenialTracking?: DenialTrackingState
  contentReplacementState?: ContentReplacementState
  renderedSystemPrompt?: SystemPrompt
}
```

### ToolPermissionContext:
```typescript
export type ToolPermissionContext = DeepImmutable<{
  mode: PermissionMode
  additionalWorkingDirectories: Map<string, AdditionalWorkingDirectory>
  alwaysAllowRules: ToolPermissionRulesBySource
  alwaysDenyRules: ToolPermissionRulesBySource
  alwaysAskRules: ToolPermissionRulesBySource
  isBypassPermissionsModeAvailable: boolean
  isAutoModeAvailable?: boolean
  strippedDangerousRules?: ToolPermissionRulesBySource
  shouldAvoidPermissionPrompts?: boolean
  awaitAutomatedChecksBeforeDialog?: boolean
  prePlanMode?: PermissionMode
}>
```
