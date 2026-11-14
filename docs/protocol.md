# A2A Protocol Reference

How gobi-a2a, the Gobi harness and any third-party agent talk to each other: the **Agent2Agent (A2A) protocol, version
1.0**, as this gateway serves it.

Every request, response and stream on this page was captured from the running gateway (`a2a-sdk` 1.3.0). The examples
keep their real shape; only IDs and long text are shortened. `tests/test_protocol.py` checks each documented behavior
in raw JSON, so if the server changes, a test fails before the reference goes stale.

**Contents**

1. [A2A: an open standard](#1-a2a-an-open-standard)
2. [What this gateway implements](#2-what-this-gateway-implements)
3. [Transport: HTTP, headers, JSON-RPC envelope](#3-transport-http-headers-json-rpc-envelope)
4. [Endpoints](#4-endpoints)
5. [The Agent Card](#5-the-agent-card)
6. [Data model](#6-data-model)
7. [Methods](#7-methods): `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, unsupported, pre-1.0 names
8. [Task lifecycle](#8-task-lifecycle): states, contexts, polling, streaming, steering, cancellation
9. [Metadata: model, effort, and cost](#9-metadata-model-effort-and-cost)
10. [Errors](#10-errors)
11. [The HTTP+JSON (REST) binding](#11-the-httpjson-rest-binding)
12. [Implementing a third-party agent](#12-implementing-a-third-party-agent)
13. [Known deviations and limits](#13-known-deviations-and-limits)
14. [curl cookbook](#14-curl-cookbook)

---

## 1. A2A: an open standard

**A2A (Agent2Agent) is an open, vendor-neutral protocol for agents to talk to each other.** One agent (the *client*)
hands work to another (the *remote agent*), follows it, steers it and stops it, without knowing how the remote agent is
built: its framework, its model, its tools, or the language it is written in.

**Governance.** Google introduced A2A in April 2025 and donated it to the **Linux Foundation** in June 2025. In August
2026 it became a hosted project of the Linux Foundation's **Agentic AI Foundation (AAIF)**, next to the Model Context
Protocol (MCP): MCP connects an agent to its tools and data, and A2A connects agents to each other. Version **1.0**
(2026) added version negotiation (the `A2A-Version` header, §3.2), multiple protocol bindings, multi-tenancy and signed
Agent Cards. The normative specification, its schemas and the official SDKs (Python, JavaScript, Java, Go, .NET) are at:

- **<https://a2a-protocol.org>**: the specification, the canonical source for everything on this page
- **<https://github.com/a2aproject>**: the spec repository and the SDKs (this gateway runs on `a2a-sdk` for Python)

Where this page and the specification disagree, the specification wins, and this page has a bug.

Because it is a standard, nothing on this page is Gobi-specific except §9's metadata keys. Any A2A client can call the
agents behind this gateway, and a Gobi harness can call any A2A agent that meets the checklist in §12.1, whoever wrote it.

| A2A concept | What it is | Here |
|---|---|---|
| **Agent Card** | A JSON document at a well-known URL that says who the agent is, what it can do, where to send work, and how to sign in | one per agent, `/<agent>/.well-known/agent-card.json` |
| **Task** | One unit of work with an ID and a lifecycle (`SUBMITTED` → `WORKING` → `COMPLETED`, ...) | one Claude Code run, or one CLI process |
| **Message** | One turn from the user (`ROLE_USER`) or the agent (`ROLE_AGENT`), made of **Parts** | the brief and steers in; progress notes out |
| **Artifact** | An output the task produced | the run's result text, named `result` |
| **Context** | A conversation: tasks that share a `contextId` belong together | one workspace folder and, for claude, one Claude session |
| **Metadata** | A free-form JSON object on messages, tasks, and requests, for extensions | the per-run `model`/`effort` in; the run's cost out |

What A2A provides, and how the gateway uses each part:

- **Task creation:** `SendMessage` with a new message.
- **Polling:** `GetTask` (or `ListTasks`).
- **Streaming:** `SendStreamingMessage` and `SubscribeToTask` over Server-Sent Events.
- **Steering:** `SendMessage` with the `taskId` of a running task.
- **Cancellation:** `CancelTask`.
- **Metadata:** `model` and `effort` per run; `cost_usd`, `usage`, `turns`, ... on the final status.

The spec defines three **bindings** (wire formats) of the same operations: **JSON-RPC 2.0** over HTTP, **HTTP+JSON**
(REST), and **gRPC**. A card lists the bindings its agent serves in `supportedInterfaces`. This gateway serves
JSON-RPC (§3–§7), the binding the Gobi harness uses. §11 documents the REST binding for third parties that choose it.

## 2. What this gateway implements

| Feature | Status |
|---|---|
| Protocol version | **1.0** (requests must say so: §3.2) |
| JSON-RPC 2.0 binding | ✅ `POST /<agent>/` |
| HTTP+JSON (REST) binding | ❌ not mounted (§11 says how to add it) |
| gRPC binding | ❌ |
| Agent Card | ✅ public, `GET /<agent>/.well-known/agent-card.json` |
| `SendMessage` (blocking, or `returnImmediately`) | ✅ |
| `SendStreamingMessage` (SSE) | ✅ `capabilities.streaming: true` |
| `GetTask`, `ListTasks`, `CancelTask` | ✅ |
| `SubscribeToTask` (SSE) | ✅ while the task is not finished |
| Push notifications (webhooks) | ❌ `capabilities.pushNotifications: false`, error `-32003` |
| Authenticated extended card | ❌ error `-32004` |
| Input modes / output modes | `text/plain` in, `text/plain` out |
| Security | HTTP Bearer (§3.3) |
| Task states produced | `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED` (never `INPUT_REQUIRED` or `AUTH_REQUIRED`) |
| Persistence | in memory: a restart forgets tasks (§13) |

## 3. Transport: HTTP, headers, JSON-RPC envelope

### 3.1 HTTP

Each agent has one JSON-RPC endpoint, the URL its card names (`<public_url>/<agent>/`, **with** the trailing slash).
Every method is an HTTP `POST` to that one URL with a JSON body.

| Request header | Value | |
|---|---|---|
| `Content-Type` | `application/json` | |
| `A2A-Version` | `1.0` | **required**, §3.2 |
| `Authorization` | `Bearer <token>` | required unless the gateway runs with `no_auth`, §3.3 |
| `Accept` | `text/event-stream` | for the streaming methods (optional; they stream either way) |

| Response | When |
|---|---|
| `200`, `application/json`, a JSON-RPC response | every call that got past auth, **including JSON-RPC errors** |
| `200`, `text/event-stream` | `SendStreamingMessage`, `SubscribeToTask` (§7.2, §7.6) |
| `401`, `{"error": "unauthorized"}`, `WWW-Authenticate: Bearer` | missing or wrong token. Not a JSON-RPC body |
| `404` | no agent at that path (disabled or unknown) |

### 3.2 Protocol version

Send `A2A-Version: 1.0` on every call. A request without it is treated as protocol `0.3`. This gateway does not accept
0.3, and every call fails with:

```json
{"error": {"code": -32009, "message": "A2A version '0.3' is not supported by this handler. Expected version '1.0'.",
           "data": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "VERSION_NOT_SUPPORTED",
                     "domain": "a2a-protocol.org", "metadata": {}}]},
 "id": 1, "jsonrpc": "2.0"}
```

The official SDK clients and the Gobi harness send the header. A hand-written client must add it.

### 3.3 Authentication

The card declares HTTP Bearer:

```json
"securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
"securityRequirements": [{"schemes": {"bearer": {}}}]
```

Every request needs `Authorization: Bearer <token>`, except `GET /healthz` and the cards. A card stays public because
a client reads it to learn how to sign in. The token is the agent's own `token` from `a2a.yaml` if it has one, else
`gateway.token`. It is compared in constant time. With `gateway.no_auth: true` and no per-agent token, the card has no
`securitySchemes` and no request needs a token.

### 3.4 The JSON-RPC 2.0 envelope

Request:

```json
{"jsonrpc": "2.0", "id": "any string or number", "method": "SendMessage", "params": { ... }}
```

Response, exactly one of `result` and `error`, with the request's `id`:

```json
{"result": { ... }, "id": "same id", "jsonrpc": "2.0"}
{"error": {"code": -32001, "message": "Task not found", "data": [ ... ]}, "id": "same id", "jsonrpc": "2.0"}
```

Conventions on the wire (the protobuf JSON mapping the spec uses):

- **Field names are lowerCamelCase** (`contextId`, `returnImmediately`, `historyLength`).
- **Enums are their full names as strings:** `"ROLE_USER"`, `"TASK_STATE_WORKING"`.
- **Empty and default fields are left out.** A task with no artifacts has no `artifacts` key, and a status with no
  message has no `message`. (`ListTasks` is an exception: it includes empty defaults such as `"extensions": []`.)
- **`metadata` is a free-form JSON object, and every number in it comes back as a double:** send `"turns": 3`, read
  `"turns": 3.0`. Parse numbers as numbers, not as integers.
- **Timestamps** are RFC 3339 in UTC: `"2026-11-14T00:44:26.787295Z"`.
- **Unknown fields in `params` are ignored**, so a newer client's extra fields do no harm.

## 4. Endpoints

The gateway's own endpoints, plus each enabled agent's A2A endpoints under `/<agent>` (`claude`, `cursor`, `codex`,
`omp`, `copilot`):

| Method and path | Auth | Body | Answer |
|---|---|---|---|
| `GET /healthz` | public | | `{"status": "ok"}` |
| `GET /` | gateway token | | the gateway and its agents: `{"name", "version", "url", "agents": [{"id", "name", "description", "address", "endpoint", "card", "auth"}], "disabled": [...]}` |
| `GET /v1/agents` | gateway token | | `{"agents": [{... as above, "agent_card": {the full card}}]}` |
| `GET /<agent>/.well-known/agent-card.json` | public | | the Agent Card (§5) |
| `POST /<agent>/` | agent's token | JSON-RPC (§7) | JSON, or SSE for the streaming methods |

`GET /` and `GET /v1/agents` belong to the gateway, not to A2A. They list what is mounted, to make discovery easier.
The A2A way to discover an agent is its card.

## 5. The Agent Card

`GET /claude/.well-known/agent-card.json`, captured:

```json
{
  "name": "Claude Code",
  "description": "Claude Code working in its own folder: code, files and web research.",
  "supportedInterfaces": [
    {"url": "https://my-mac.tail1234.ts.net/claude/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
  ],
  "version": "1",
  "capabilities": {"streaming": true, "pushNotifications": false},
  "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
  "securityRequirements": [{"schemes": {"bearer": {}}}],
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {"id": "work", "name": "Work on files and the web",
     "description": "Reads, writes and edits files in its own workspace, ... A message may pick the run's model and effort: {\"model\": ..., \"effort\": \"low\"|...|\"max\"} in its metadata; otherwise the server's defaults.",
     "tags": ["coding", "research", "files"],
     "examples": ["Write a script that ...", "Find out ... and summarize it"]}
  ]
}
```

| Field | Type | |
|---|---|---|
| `name` | string | **required**. Shown in the Gobi app |
| `description` | string | **required**. The harness shows it to its super agent, which decides what to hand over |
| `supportedInterfaces[]` | `{url, protocolBinding, protocolVersion, tenant?}` | **required**. Where to send work, in which binding. `protocolBinding` is `"JSONRPC"`, `"HTTP+JSON"` or `"GRPC"` |
| `version` | string | the agent's own version, not the protocol's |
| `capabilities` | `{streaming?, pushNotifications?, extendedAgentCard?, extensions?[]}` | optional features |
| `securitySchemes` | map name → scheme | `httpAuthSecurityScheme` (`Bearer`, `Basic`), `apiKeySecurityScheme` (`{location, name}`), OAuth2, OpenID Connect, mTLS |
| `securityRequirements[]` | `{schemes: {name: {list: [scopes]}}}` | which schemes a request must satisfy |
| `defaultInputModes[]`, `defaultOutputModes[]` | MIME types | |
| `skills[]` | `{id, name, description, tags[], examples?[], inputModes?[], outputModes?[]}` | **required**, at least one. What the agent is good at |
| `provider`, `documentationUrl`, `iconUrl`, `signatures[]` | | optional; not set here |

Rules a client should follow, and the gateway relies on:

- **Location.** Given an address, the card is at `<address>/.well-known/agent-card.json`. The address for a gateway
  agent is `<public_url>/<agent>`.
- **Endpoint resolution.** Resolve the chosen interface's `url` against the card's URL. The gateway always gives an
  absolute URL ending in `/`. Send work to exactly that URL: the gateway would redirect `/claude` to `/claude/`, and the
  harness follows no redirects.
- **Caching.** The gateway sends no `Cache-Control` on cards. The harness then keeps a card for a day and reads it again.

## 6. Data model

The JSON shapes of A2A 1.0's core types, in TypeScript notation. `?` means optional. Fields this gateway never sets are
marked *(not used)*, but a client should accept them from other agents.

```ts
type Role = "ROLE_USER" | "ROLE_AGENT";

type TaskState =
  | "TASK_STATE_SUBMITTED"      // accepted, not started
  | "TASK_STATE_WORKING"        // running
  | "TASK_STATE_INPUT_REQUIRED" // paused: the agent waits for the caller's answer   (not used here)
  | "TASK_STATE_AUTH_REQUIRED"  // paused: the agent waits for a sign-in             (not used here)
  | "TASK_STATE_COMPLETED"      // terminal: done
  | "TASK_STATE_FAILED"         // terminal: tried, and could not
  | "TASK_STATE_CANCELED"       // terminal: stopped by CancelTask
  | "TASK_STATE_REJECTED";      // terminal: refused before it started

interface Part {                // exactly one of text | raw | url | data
  text?: string;                // the only kind this gateway reads or writes
  raw?: string;                 // bytes, base64                                     (not used)
  url?: string;                 // a file by reference                               (not used)
  data?: any;                   // structured JSON                                   (not used)
  filename?: string;
  mediaType?: string;           // e.g. "text/plain", "image/png"
  metadata?: object;
}

interface Message {
  messageId: string;            // REQUIRED, chosen by the sender, unique per message
  role: Role;                   // REQUIRED
  parts: Part[];                // REQUIRED
  contextId?: string;           // the conversation; set it to continue one
  taskId?: string;              // set it to steer that task
  metadata?: object;            // here: {"model"?, "effort"?} in, cost and usage out (§9)
  referenceTaskIds?: string[];  // (not used)
  extensions?: string[];        // (not used)
}

interface TaskStatus {
  state: TaskState;
  message?: Message;            // the agent's latest word: a progress note, or the final answer
  timestamp?: string;           // RFC 3339, UTC
}

interface Artifact {
  artifactId: string;
  name?: string;                // here: "result"
  description?: string;
  parts: Part[];
  metadata?: object;
  extensions?: string[];
}

interface Task {
  id: string;                   // assigned by the agent
  contextId: string;            // the caller's, or assigned by the agent
  status: TaskStatus;
  artifacts?: Artifact[];       // here: one, "result", when the task completes
  history?: Message[];          // the caller's messages and the agent's notes, in order
  metadata?: object;
}

// streaming events (§7.2)
interface TaskStatusUpdateEvent   { taskId: string; contextId: string; status: TaskStatus; metadata?: object }
interface TaskArtifactUpdateEvent { taskId: string; contextId: string; artifact: Artifact;
                                    append?: boolean; lastChunk?: boolean; metadata?: object }
```

## 7. Methods

All examples below go to `POST https://my-mac.tail1234.ts.net/claude/` with the headers of §3.1.

### 7.1 `SendMessage`: create a task, continue a conversation, or steer a task

**Params** (`SendMessageRequest`):

```ts
{
  message: Message;                         // REQUIRED
  configuration?: {
    returnImmediately?: boolean;            // false (default): answer when the task ends. true: answer at once
    historyLength?: number;                 // how much history to return
    acceptedOutputModes?: string[];         // MIME types the caller can read
    taskPushNotificationConfig?: object;    // (not supported here)
  };
  metadata?: object;                        // request-level metadata; the message's wins (§9.1)
}
```

**Result:** `{"task": Task}` (or, from agents that answer without creating a task, `{"message": Message}`; the
gateway's agents always create a task).

What the message means depends on its IDs:

| `message.taskId` | `message.contextId` | Meaning |
|---|---|---|
| absent | absent | **a new task in a new conversation**; the agent assigns both IDs |
| absent | set | **a new task in that conversation**: a follow-up. Same workspace; claude resumes the same Claude session. A `contextId` the caller makes up is kept as is |
| a running task | (its context) | **a steer**: §8.5 |
| a finished task | | error `-32004` "Task ... is in terminal state" |

**Example: blocking.** The answer comes when the run ends:

```json
{"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {
  "message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "write a.txt"}],
              "metadata": {"model": "claude-opus-5-5", "effort": "xhigh"}}}}
```

```json
{"result": {"task": {
  "id": "0b7d77cc-...", "contextId": "7b9beb18-...",
  "status": {
    "state": "TASK_STATE_COMPLETED",
    "message": {"messageId": "e2f5b6fd-...", "contextId": "7b9beb18-...", "taskId": "0b7d77cc-...", "role": "ROLE_AGENT",
                "parts": [{"text": "Did: write a.txt"}],
                "metadata": {"cost_usd": 0.042, "usage": {"input_tokens": 1500.0, "output_tokens": 200.0},
                             "turns": 3.0, "duration_ms": 1200.0, "model": "claude-opus-5-5", "effort": "xhigh"}},
    "timestamp": "2026-11-14T00:44:26.787295Z"},
  "artifacts": [{"artifactId": "f1abe3bd-...", "name": "result", "parts": [{"text": "Did: write a.txt"}]}],
  "history": [
    {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "write a.txt"}],
     "metadata": {"model": "claude-opus-5-5", "effort": "xhigh"}, "contextId": "7b9beb18-...", "taskId": "0b7d77cc-..."},
    {"messageId": "e419a678-...", "role": "ROLE_AGENT", "parts": [{"text": "Looking at the files."}], "...": "..."},
    {"messageId": "7ea5da75-...", "role": "ROLE_AGENT", "parts": [{"text": "[Read]"}], "...": "..."}]
}}, "id": 1, "jsonrpc": "2.0"}
```

**Example: `returnImmediately`.** This is what the Gobi harness sends. The task comes back at once in `SUBMITTED`;
follow it with `GetTask` (§7.3) or `SubscribeToTask` (§7.6):

```json
{"jsonrpc": "2.0", "id": 2, "method": "SendMessage", "params": {
  "message": {"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "long job"}]},
  "configuration": {"returnImmediately": true}}}
```

```json
{"result": {"task": {"id": "515c2f6f-...", "contextId": "53c9c756-...", "status": {"state": "TASK_STATE_SUBMITTED"},
  "history": [{"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "long job"}], "...": "..."}]}},
 "id": 2, "jsonrpc": "2.0"}
```

A long run should always use `returnImmediately` (or streaming). A blocking call holds its HTTP request open for the
whole run, and the run may outlast any timeout between client and agent: the harness's own is 60 s, and tunnels have
theirs.

### 7.2 `SendStreamingMessage`: create a task and stream it

**Params:** as `SendMessage`. **Answer:** `200 text/event-stream`. Each event is one `data:` line holding a complete
JSON-RPC response with the request's `id`. Its `result` holds exactly one of `task`, `statusUpdate` (a
`TaskStatusUpdateEvent`), `artifactUpdate` (a `TaskArtifactUpdateEvent`) or `message`. The stream ends after the event
with a terminal state.

```http
POST /claude/ HTTP/1.1
Content-Type: application/json
Accept: text/event-stream
A2A-Version: 1.0
Authorization: Bearer <token>

{"jsonrpc": "2.0", "id": 9, "method": "SendStreamingMessage",
 "params": {"message": {"messageId": "m-3", "role": "ROLE_USER", "parts": [{"text": "stream it"}]}}}
```

```text
HTTP/1.1 200 OK
content-type: text/event-stream; charset=utf-8

data: {"result": {"task": {"id": "2498d0f4-...", "contextId": "e573a8a9-...", "status": {"state": "TASK_STATE_SUBMITTED"}, "history": [...]}}, "id": 9, "jsonrpc": "2.0"}

data: {"result": {"statusUpdate": {"taskId": "2498d0f4-...", "contextId": "e573a8a9-...", "status": {"state": "TASK_STATE_WORKING", "timestamp": "..."}}}, "id": 9, "jsonrpc": "2.0"}

data: {"result": {"statusUpdate": {"taskId": "2498d0f4-...", "contextId": "e573a8a9-...", "status": {"state": "TASK_STATE_WORKING", "message": {"role": "ROLE_AGENT", "parts": [{"text": "Looking at the files."}], "...": "..."}, "timestamp": "..."}}}, "id": 9, "jsonrpc": "2.0"}

data: {"result": {"statusUpdate": {"taskId": "2498d0f4-...", "contextId": "e573a8a9-...", "status": {"state": "TASK_STATE_WORKING", "message": {"role": "ROLE_AGENT", "parts": [{"text": "[Read]"}], "...": "..."}, "timestamp": "..."}}}, "id": 9, "jsonrpc": "2.0"}

data: {"result": {"artifactUpdate": {"taskId": "2498d0f4-...", "contextId": "e573a8a9-...", "artifact": {"artifactId": "ca04ab24-...", "name": "result", "parts": [{"text": "Did: stream it"}]}}}, "id": 9, "jsonrpc": "2.0"}

data: {"result": {"statusUpdate": {"taskId": "2498d0f4-...", "contextId": "e573a8a9-...", "status": {"state": "TASK_STATE_COMPLETED", "message": {"role": "ROLE_AGENT", "parts": [{"text": "Did: stream it"}], "metadata": {"cost_usd": 0.042, "usage": {"...": "..."}, "model": "claude-sonnet-5", "effort": "high", "...": "..."}}, "timestamp": "..."}}}, "id": 9, "jsonrpc": "2.0"}
```

The order is always: `task` (SUBMITTED), then `statusUpdate` WORKING, then one `statusUpdate` WORKING per progress note,
then `artifactUpdate` (on success), then the terminal `statusUpdate`. A client that loses the stream can reconnect with
`SubscribeToTask` while the task runs, or read the end with `GetTask`.

### 7.3 `GetTask`: poll a task

**Params:** `{"id": string, "historyLength"?: number}`. `historyLength` keeps only the last *n* history messages; `0`
leaves out `history`. **Result:** the `Task` (not wrapped in `{"task": ...}`).

```json
{"jsonrpc": "2.0", "id": 3, "method": "GetTask", "params": {"id": "515c2f6f-...", "historyLength": 2}}
```

```json
{"result": {"id": "515c2f6f-...", "contextId": "53c9c756-...",
  "status": {"state": "TASK_STATE_WORKING",
             "message": {"role": "ROLE_AGENT", "parts": [{"text": "[Read]"}], "...": "..."},
             "timestamp": "2026-11-14T00:44:26.789275Z"},
  "history": [{"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "long job"}], "...": "..."},
              {"role": "ROLE_AGENT", "parts": [{"text": "Looking at the files."}], "...": "..."}]},
 "id": 3, "jsonrpc": "2.0"}
```

While the task runs, `status.message` is its latest progress note. Once it ends, `status.message` is the final word
(with the cost metadata, §9.2), and `artifacts` holds the result. Unknown ID: `-32001`.

### 7.4 `ListTasks`: list tasks, paged

**Params:** `{"contextId"?, "status"?: TaskState, "pageSize"?: 1–100 (default 50), "pageToken"?, "historyLength"?,
"includeArtifacts"?: boolean (default false), "statusTimestampAfter"?: timestamp}`.
**Result:** `{"tasks": Task[], "pageSize": number, "totalSize": number, "nextPageToken": string}`. Pass
`nextPageToken` back as `pageToken` for the next page; it is `""` on the last page. Tasks come without `artifacts`
unless `includeArtifacts` is true.

```json
{"jsonrpc": "2.0", "id": 4, "method": "ListTasks", "params": {"contextId": "53c9c756-...", "pageSize": 2}}
```

### 7.5 `CancelTask`: stop a task

**Params:** `{"id": string, "metadata"?: object}`. **Result:** the `Task`, now `TASK_STATE_CANCELED`. The run is stopped:
the Claude Code run is cancelled, or the CLI process gets SIGTERM and then SIGKILL.

```json
{"jsonrpc": "2.0", "id": 5, "method": "CancelTask", "params": {"id": "515c2f6f-..."}}
```

```json
{"result": {"id": "515c2f6f-...", "contextId": "53c9c756-...",
  "status": {"state": "TASK_STATE_CANCELED",
             "message": {"role": "ROLE_AGENT", "parts": [{"text": "Stopped before it finished."}], "...": "..."},
             "timestamp": "2026-11-14T00:44:30.999449Z"},
  "history": [ ... ]}, "id": 5, "jsonrpc": "2.0"}
```

A task that has already ended: `-32002` "Task cannot be canceled". Unknown ID: `-32001`. See §13 for a cancel while a
steer is queued.

### 7.6 `SubscribeToTask`: stream a task that is already running

**Params:** `{"id": string}`. **Answer:** an SSE stream as in §7.2: first the `task` as it is now, then its events until
it ends. On a task that has already ended: `-32004` "Task ... is in terminal state".

### 7.7 Not supported here

| Method | Error |
|---|---|
| `CreateTaskPushNotificationConfig`, `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig` | `-32003` push notifications are not supported |
| `GetExtendedAgentCard` | `-32004` the agent does not support authenticated extended cards |
| anything else | `-32601` Method not found |

### 7.8 Method names from earlier versions (`tasks/send`, `tasks/get`, ...)

Drafts of A2A before 1.0 named the JSON-RPC methods with slashes, and much older material (blog posts, early SDKs) still
uses those names. **1.0 renamed them, and this gateway serves only the 1.0 names.** An old name gets `-32601` "Method
not found", with or without the `A2A-Version` header. A client written for an earlier draft must move to the names
below and send `A2A-Version: 1.0`.

| 1.0 (served here) | 0.2 / 0.3 | 0.1 |
|---|---|---|
| `SendMessage` | `message/send` | `tasks/send` |
| `SendStreamingMessage` | `message/stream` | `tasks/sendSubscribe` |
| `GetTask` | `tasks/get` | `tasks/get` |
| `ListTasks` | | |
| `CancelTask` | `tasks/cancel` | `tasks/cancel` |
| `SubscribeToTask` | `tasks/resubscribe` | `tasks/resubscribe` |
| `CreateTaskPushNotificationConfig`, `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs`, `DeleteTaskPushNotificationConfig` | `tasks/pushNotificationConfig/set`, `/get`, `/list`, `/delete` | `tasks/pushNotification/set`, `/get` |
| `GetExtendedAgentCard` | `agent/getAuthenticatedExtendedCard` | |

The data changed with the names. In 1.0, enums are upper-case protobuf names (`ROLE_USER`, `TASK_STATE_COMPLETED`)
where earlier drafts used lower-case strings (`user`, `completed`, `input-required`), parts are `{"text": ...}` rather
than `{"kind": "text", "text": ...}`, and the card's endpoints moved into `supportedInterfaces` (§5). The card itself is
at `/.well-known/agent-card.json`; the earliest drafts used `/.well-known/agent.json`, which this gateway does not
serve (`404` with a token; without one, `401`, because only `agent-card.json` is public).

## 8. Task lifecycle

### 8.1 States

```text
                 SendMessage
                     │
                     ▼
               ┌───────────┐  empty text, or a bad model/effort    ┌──────────┐
               │ SUBMITTED │ ─────────────────────────────────────▶│ REJECTED │
               └─────┬─────┘                                       └──────────┘
                     │ the run starts
                     ▼
               ┌───────────┐ ◀─┐ a progress note: a new status message, state unchanged
               │  WORKING  │ ──┘ (a steer queued: the task stays WORKING and runs again, §8.5)
               └─────┬─────┘
       ┌─────────────┼──────────────────────┐
       ▼             ▼                      ▼
┌───────────┐  ┌──────────┐          ┌──────────┐
│ COMPLETED │  │  FAILED  │          │ CANCELED │ ◀── CancelTask
└───────────┘  └──────────┘          └──────────┘
 + artifact     the error,            "Stopped before it finished."
 + cost         + cost if any
```

| State | Terminal | When the gateway's agents enter it |
|---|---|---|
| `SUBMITTED` | | the task was created and is queued |
| `WORKING` | | the run started; each note is a new `status.message` |
| `COMPLETED` | ✅ | the run succeeded: the `result` artifact, the answer, the cost |
| `FAILED` | ✅ | claude: an error result (e.g. `error_max_turns`) or an exception. CLI: a nonzero exit, a missing program, a timeout |
| `CANCELED` | ✅ | `CancelTask` stopped it |
| `REJECTED` | ✅ | refused before anything ran: no text, or a `model`/`effort` that cannot be used (§9.1) |
| `INPUT_REQUIRED`, `AUTH_REQUIRED` | (interrupted) | never here. Other agents may use them to pause and ask; the Gobi harness treats both as "blocked" and ends the ticket |

A terminal task does not change again (but see §13), and it takes no more messages (`-32004`). Continue the
conversation with a **new task in the same context**.

### 8.2 Contexts and follow-ups

`contextId` groups tasks into a conversation. The gateway gives each context a workspace folder
(`<workdir>/<agent>/<contextId>/`), and for claude the Claude session too. A new task with the same `contextId`
therefore works in the same folder and continues the same Claude session. A new task without one starts fresh. The
caller may make up a `contextId` (e.g. its own thread ID); it is kept as is (and made safe for a folder name).

### 8.3 Polling

Send with `returnImmediately: true`, then call `GetTask` until `status.state` is terminal (or `INPUT_REQUIRED` /
`AUTH_REQUIRED` from other agents). The Gobi harness waits 2 s before the first look and grows the wait ×1.5 up to
15 s. A harness-side deadline cancels the task with `CancelTask`. While polling, `status.message` is the latest progress
note: a line of Claude's text, `"[ToolName]"` for each tool it starts, or a line a CLI printed.

### 8.4 Streaming

`SendStreamingMessage` or `SubscribeToTask` push the same events a poller would see, as they happen (§7.2). Streaming
and polling mix freely: a task created by a stream can be read with `GetTask`, and the other way round.

### 8.5 Steering

A **steer** is a `SendMessage` whose `message.taskId` is a task that is still running:

```json
{"jsonrpc": "2.0", "id": 6, "method": "SendMessage", "params": {
  "message": {"messageId": "m-4", "role": "ROLE_USER", "parts": [{"text": "go deeper"}],
              "taskId": "515c2f6f-...", "contextId": "53c9c756-...", "metadata": {"effort": "max"}},
  "configuration": {"returnImmediately": true}}}
```

With `returnImmediately`, the answer is the task as it is now (still `WORKING`). The steer is queued and does not
interrupt the current run. When that run ends, the agent runs again on the same task with the steer's text: claude
resumes the same Claude session, and a CLI agent starts its program again in the same folder. The steer's own
`metadata` picks that run's model and effort (§9.1). Without `returnImmediately`, the call waits until the run it
queued behind has ended.

What a steered task looks like when it ends:

- `status` is the **last** run's: its state, its answer in `status.message`, and its cost in that message's `metadata`.
  Each run's cost is reported on its own, so a client summing costs should read the status after every run, or count
  the last one only.
- `artifacts` has **one `result` per run**, oldest first (e.g. `"Did: first"`, then `"Did: second"`). A client that
  wants only the final answer should read the last artifact, or `status.message`. The Gobi harness joins all
  artifacts' text into its report.

### 8.6 Cancellation

`CancelTask` stops the running run. Claude Code's run is cancelled; a CLI's process group gets SIGTERM, then SIGKILL
after 5 s. The task becomes `CANCELED` with the note "Stopped before it finished.". Canceling a finished task is an
error (`-32002`). Files the run wrote stay in its folder.

## 9. Metadata: model, effort, and cost

`metadata` is A2A's extension point. These keys are this gateway's (and the Gobi harness's) convention, not part of
the core spec. Other A2A clients may ignore them, and other agents may simply not send them.

### 9.1 In: choosing the model and effort for one run

| Key | Value | |
|---|---|---|
| `model` | a model name, matching `[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}`, e.g. `claude-opus-5-5`, `claude-sonnet-5`, `opus`, `claude-x[1m]`, a Bedrock ID | the run's model instead of `a2a.yaml`'s |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` (case-insensitive) | the run's effort instead of `a2a.yaml`'s |

- **Where.** `message.metadata`, or the request's `params.metadata`. Each key is taken from the message if it has one,
  else from the request.
- **Not sticky.** A key a message leaves out (or sends as `""`/`null`) is the server's default, not what the previous
  run used. A follow-up or a steer names its own each time; the Gobi harness sends the ticket's current choice with
  every message.
- **Checked before anything runs.** A value that is not a model name, or not one of the efforts (including non-strings
  such as `3`), makes the task `REJECTED` with "Not run: ...".
- **CLI agents** pass them through `model_args` / `effort_args` (`a2a.yaml`), e.g. `--model gpt-5`.

### 9.2 Out: what the run cost

The final status message's `metadata` (on `COMPLETED`, and on `FAILED` when the run got as far as a result):

| Key | Type | | Agent |
|---|---|---|---|
| `cost_usd` | number | the run's cost in US dollars | claude |
| `usage` | object | token counts: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, ... | claude |
| `turns` | number | Claude's turns in the run | claude |
| `duration_ms` | number | wall time of the run | claude |
| `model` | string | the model that ran (the caller's choice or the default) | all, when set |
| `effort` | string | the effort that ran; absent when Claude Code's default was used | all, when set |
| `exit_code` | number | the program's exit status | CLI agents |

The status message stays on the task, so `GetTask` at any later time returns the same numbers. The Gobi harness adds
`cost_usd` to the worker's spend. It counts `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`
as input, `cache_read_input_tokens` as cached, and `output_tokens` as output. A third-party agent that wants its cost
counted in Gobi sends these keys.

## 10. Errors

**JSON-RPC errors** come with HTTP 200. `data` is a list of `google.rpc.ErrorInfo` objects with a machine-readable
`reason` and `domain: "a2a-protocol.org"`:

| JSON-RPC code | Reason | REST (§11) | When |
|---|---|---|---|
| `-32700` | | | the body is not JSON (`id` is `null`) |
| `-32600` | `INVALID_REQUEST` | 400 `INVALID_ARGUMENT` | not a valid JSON-RPC request |
| `-32601` | `METHOD_NOT_FOUND` | 404 `NOT_FOUND` | unknown method |
| `-32602` | `INVALID_PARAMS` | 400 `INVALID_ARGUMENT` | params do not fit the method, e.g. a message without `messageId`, `parts` not a list. `data[0].metadata` says which field |
| `-32603` | `INTERNAL_ERROR` | 500 `INTERNAL` | internal error |
| `-32001` | `TASK_NOT_FOUND` | 404 `NOT_FOUND` | no task with that ID (or it was forgotten in a restart) |
| `-32002` | `TASK_NOT_CANCELABLE` | 400 `FAILED_PRECONDITION` | `CancelTask` on a finished task |
| `-32003` | `PUSH_NOTIFICATION_NOT_SUPPORTED` | 400 `FAILED_PRECONDITION` | any push notification method |
| `-32004` | `UNSUPPORTED_OPERATION` | 400 `FAILED_PRECONDITION` | a message to, or a subscription on, a finished task; `GetExtendedAgentCard` |
| `-32005` | `CONTENT_TYPE_NOT_SUPPORTED` | 400 `INVALID_ARGUMENT` | input or output modes the agent cannot handle |
| `-32006` | `INVALID_AGENT_RESPONSE` | 500 `INTERNAL` | the agent produced something invalid |
| `-32007` | `EXTENDED_AGENT_CARD_NOT_CONFIGURED` | 400 `FAILED_PRECONDITION` | |
| `-32008` | `EXTENSION_SUPPORT_REQUIRED` | 400 `FAILED_PRECONDITION` | |
| `-32009` | `VERSION_NOT_SUPPORTED` | 400 `FAILED_PRECONDITION` | no `A2A-Version: 1.0` header (§3.2) |

```json
{"error": {"code": -32602, "message": "Validation failed",
           "data": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "INVALID_PARAMS",
                     "domain": "a2a-protocol.org",
                     "metadata": {"errors": [{"field": "message.message_id", "message": "Field is required."}]}}]},
 "id": 1, "jsonrpc": "2.0"}
```

**A task that ends badly is not an error.** A run that fails comes back as a successful JSON-RPC response whose task is
`FAILED` or `REJECTED`, with the reason in `status.message`.

**HTTP errors:** `401` for auth (§3.3) and `404` for a path with no agent. The Gobi harness treats `404` as "the card
may have moved the endpoint": it reads the card again, once.

## 11. The HTTP+JSON (REST) binding

The same operations as plain HTTP resources, for clients and agents that prefer REST to JSON-RPC. **The gateway does
not mount this binding**; everything above is JSON-RPC. The routes below are what the A2A Python SDK serves with
`a2a.server.routes.create_rest_routes(handler)`, captured from it. The same version header applies, and the same
bodies (`SendMessageRequest`, `Task`, ...) without the JSON-RPC envelope.

| Method and path | Operation | Body / query | Answer |
|---|---|---|---|
| `POST /message:send` | SendMessage | `SendMessageRequest` | `{"task": Task}` or `{"message": Message}` |
| `POST /message:stream` | SendStreamingMessage | `SendMessageRequest` | SSE: `data: {"task" \| "statusUpdate" \| "artifactUpdate" \| "message": ...}` |
| `GET /tasks/{id}` | GetTask | `?historyLength=` | `Task` |
| `GET /tasks` | ListTasks | `?contextId=&status=&pageSize=&pageToken=&historyLength=&includeArtifacts=` | `{"tasks", "nextPageToken", "pageSize", "totalSize"}` |
| `POST /tasks/{id}:cancel` | CancelTask | `{}` | `Task` |
| `GET` or `POST /tasks/{id}:subscribe` | SubscribeToTask | | SSE |
| `POST /tasks/{id}/pushNotificationConfigs` | CreateTaskPushNotificationConfig | config | config |
| `GET /tasks/{id}/pushNotificationConfigs` | ListTaskPushNotificationConfigs | | list |
| `GET`, `DELETE /tasks/{id}/pushNotificationConfigs/{configId}` | Get / DeleteTaskPushNotificationConfig | | config / empty |
| `GET /extendedAgentCard` | GetExtendedAgentCard | | `AgentCard` |

Every route is also served under a `/{tenant}` prefix for multi-tenant agents. The differences from JSON-RPC:

- Success is `200` with `Content-Type: application/a2a+json`, and the body is the bare result (no `jsonrpc`, `id` or
  `result` wrapper).
- Streams are `text/event-stream`, and each `data:` line is the bare `StreamResponse` (`{"statusUpdate": ...}`).
- **Errors use HTTP status codes**, with a `google.rpc.Status`-style body:

  ```json
  HTTP 404  {"error": {"code": 404, "status": "NOT_FOUND", "message": "Task not found",
                       "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "TASK_NOT_FOUND",
                                    "domain": "a2a-protocol.org", "metadata": {}}]}}
  HTTP 400  {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "Task cannot be canceled", "details": [...]}}
  HTTP 400  {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "A2A version '0.3' is not supported ...", "details": [...]}}
  ```

- The card's interface says `"protocolBinding": "HTTP+JSON"` and its `url` is the base the paths above are relative to.

To serve REST from the gateway as well, add `create_rest_routes(handler)` to `a2a_app()` in
`gobi_a2a/agents/common.py`, and a second `AgentInterface(protocol_binding="HTTP+JSON")` to `card()`. The Gobi harness
would still pick JSON-RPC.

## 12. Implementing a third-party agent

Any program that speaks A2A can be a Gobi worker: in any language, on any host, with or without this gateway. There
are three ways to build one.

### 12.1 What a Gobi harness needs from an agent

This is the subset of A2A that `gobi-harness/engine/a2a.py` uses. An agent that meets it works as a Gobi worker:

1. **Reachable over public `https://`.** The harness refuses private addresses and follows **no redirects**.
2. **A card at `<address>/.well-known/agent-card.json`:** `200`, JSON, at most 64 KB, with a string `name`. It also
   needs a `supportedInterfaces` entry with `"protocolBinding": "JSONRPC"` and a `url` (a top-level `url`, the 0.3
   style, also works). The `description` and `skills` are shown to the user and to the super agent.
3. **Sign-in:** none, HTTP Bearer, HTTP Basic, or an API key in a header, as declared in `securitySchemes` /
   `securityRequirements`. Anything else (OAuth2, OpenID Connect, mTLS) is refused when the worker is added.
4. **JSON-RPC 2.0** at that URL, answering within **60 s** per call, at most **2 MB** per answer:
   - `SendMessage` with `configuration.returnImmediately: true` → `{"task": Task}` (a bare `{"message": ...}` is taken
     as the whole, finished answer);
   - `GetTask` `{"id"}` → `Task`, polled every 2–15 s;
   - `SendMessage` with `message.taskId` (a steer) on a running task;
   - `CancelTask` `{"id"}` on kill or deadline.
5. **Auth failures as HTTP `401`/`403`**; a moved endpoint as `404` (the harness reads the card again, once).
6. **End states:** `COMPLETED` is a success. `INPUT_REQUIRED` and `AUTH_REQUIRED` are "blocked". `CANCELED` is
   "dropped". Anything else terminal is "failed". The report is the artifacts' text, or `status.message`'s if there are
   no artifacts.
7. **Optional cost:** `cost_usd` and `usage` on the final status message's `metadata` (§9.2).
8. **Optional choice:** honour `model` / `effort` from `message.metadata` (§9.1).

The harness sends `A2A-Version: 1.0`, `Content-Type: application/json`, and its credential on every call.

### 12.2 In Python, with the A2A SDK

[`examples/echo_agent.py`](../examples/echo_agent.py) is a complete, standalone agent of about 100 lines. It depends
only on the open-source `a2a-sdk`, and `tests/test_protocol.py` drives it the way the harness does. The core is an
`AgentExecutor` that turns one task into events:

```python
class Reverser(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:                                          # a new task: announce it (SUBMITTED)
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue, task.id, task.context_id)
        text = context.get_user_input().strip()
        if not text:
            await up.reject(up.new_agent_message([new_text_part("Nothing to do.")]))           # REJECTED
            return
        await up.start_work()                                                                   # WORKING
        await up.update_status(TaskState.TASK_STATE_WORKING,
                               up.new_agent_message([new_text_part("Reversing ...")]))          # a progress note
        await up.add_artifact([new_text_part(text[::-1])], name="result")                      # the result
        await up.complete(up.new_agent_message([new_text_part(text[::-1])],
                                               metadata={"cost_usd": 0.0, "usage": {...}}))     # COMPLETED + cost

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        ...                                                       # stop the work; then mark the task canceled
```

Then serve it with a card and the JSON-RPC routes:

```python
handler = DefaultRequestHandler(agent_executor=Reverser(), task_store=InMemoryTaskStore(), agent_card=card)
app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/"))
```

```sh
pip install "a2a-sdk[http-server]>=1.3,<2" uvicorn
ECHO_TOKEN=$(openssl rand -hex 24) ECHO_PUBLIC_URL=https://<your-domain> python examples/echo_agent.py
```

The SDK handles the envelope, validation, the task store, SSE, history, `GetTask`/`ListTasks`/`CancelTask`, and
queuing steers. For long work, run it in an `asyncio.Task` that `cancel()` can stop, as `gobi_a2a/agents/claude.py`
and `cli.py` do. For other languages, the official SDKs follow the same shape (an executor, a task store, a card); see
<https://a2a-protocol.org>.

### 12.3 In any language, from scratch

Without an SDK, an agent is an HTTP server with two routes:

1. `GET /.well-known/agent-card.json` → the card (§5), with `supportedInterfaces[0].url` set to your public URL.
2. `POST /` → a JSON-RPC 2.0 dispatcher (§3.4):
   - `SendMessage`: check `params.message` (`messageId`, `role`, `parts` are required). If `message.taskId` names a
     running task, queue the text for it. Otherwise create a task (new `id`; `contextId` from the message or new),
     store it as `SUBMITTED`, start the work in the background, and return `{"task": ...}` right away when
     `configuration.returnImmediately` is true.
   - Have the work update the stored task: `WORKING` with a `status.message` per progress note (and append each to
     `history`), then a terminal state with the final `status.message` (cost in its `metadata`) and the `artifacts`.
   - `GetTask`: return the stored task (trimmed to `historyLength`), or error `-32001`.
   - `CancelTask`: stop the work and set `CANCELED`, or error `-32002` if it already ended, or `-32001` if unknown.
   - Anything else: `-32601`. Malformed params: `-32602`.
3. Check `Authorization` on everything but the card, and answer `401` without it.
4. Optional: `SendStreamingMessage` / `SubscribeToTask` as `text/event-stream` (§7.2), and `capabilities.streaming: true`
   on the card.

Use the shapes in §6 exactly: camelCase names, enum names as strings, and `parts` as a list of objects with `text`.

### 12.4 Inside this gateway

To serve a new agent at `/<name>` beside claude, with the gateway's config, auth, tunnel and discovery:

- **A command line program:** add `gobi_a2a/agents/<name>.py` with a `Config(CLIConfig)` that sets the default
  `command` and a `build()` that calls `build_cli()`, as `cursor.py` does (about 30 lines). It gets one task per run,
  progress notes, cancel, timeout, and model/effort pass-through for free.
- **Anything richer** (structured events, sessions, its own cost reporting): write an `AgentExecutor` like
  `ClaudeExecutor` in `claude.py`, plus a `Config(AgentConfig)` and a `build()` that returns `Mounted(...)`.

Then register it in `AGENTS` (`gobi_a2a/agents/__init__.py`), add its block to `a2a.example.yaml`, and add tests. The
README's "Adding an agent" has the details.

### 12.5 Checking an agent

- `tests/test_protocol.py`'s `Harness` class repeats the harness's calls. Point it at your agent's ASGI app (as
  `test_the_third_party_example_speaks_what_the_harness_needs` does) or at its URL.
- By hand, with the curl calls in §14.
- With any A2A client, e.g. the Python SDK's `a2a.client.create_client(card, ...)`.

## 13. Known deviations and limits

- **A steer queued behind a canceled run still runs.** If a steer is waiting (§8.5) when `CancelTask` arrives, the
  cancel answers `CANCELED`, but the queued steer then starts a new run on the same task, which ends in `COMPLETED` (or
  `FAILED`). So the task leaves a terminal state, and work, possibly costly, happens after the cancel. The cancel answer
  can also take several seconds in this case. The behavior comes from the original `gobi-harness/a2a/claude` executor
  and is unchanged here. `tests/test_protocol.py::test_a_canceled_task_stays_canceled_even_with_a_steer_queued` records
  it as an expected failure, so it shows up once fixed. The Gobi harness stops polling after its cancel and never sees
  the later state, but the run does happen.
- **In-memory state.** Tasks, their history and Claude sessions live in the process; a restart forgets them (`GetTask`
  then says `-32001`). Workspace folders stay.
- **Text only.** Parts other than `text` are ignored on input; output is text.
- **No push notifications**, no extended card, no REST or gRPC binding, no protocol 0.3 compatibility.
- **One run at a time per task.** Steers queue rather than interrupt; to change course now, cancel and send a new task
  in the same context.

## 14. curl cookbook

```sh
A=https://my-mac.tail1234.ts.net/claude                      # an agent's address
H=(-H "Authorization: Bearer $A2A_TOKEN" -H "A2A-Version: 1.0" -H "Content-Type: application/json")

# the card (public)
curl -s $A/.well-known/agent-card.json | python3 -m json.tool

# start a task, answer at once
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{
  "message":{"messageId":"'$(uuidgen)'","role":"ROLE_USER","parts":[{"text":"List the files here"}],
             "metadata":{"model":"claude-sonnet-5","effort":"low"}},
  "configuration":{"returnImmediately":true}}}'

# poll it
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":2,"method":"GetTask","params":{"id":"<task id>","historyLength":0}}'

# steer it
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":3,"method":"SendMessage","params":{
  "message":{"messageId":"'$(uuidgen)'","role":"ROLE_USER","parts":[{"text":"Only the .py files"}],"taskId":"<task id>"},
  "configuration":{"returnImmediately":true}}}'

# follow up in the same conversation (a new task)
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":4,"method":"SendMessage","params":{
  "message":{"messageId":"'$(uuidgen)'","role":"ROLE_USER","parts":[{"text":"Now count them"}],"contextId":"<context id>"}}}'

# stream a task (-N: no buffering)
curl -sN "${H[@]}" -H "Accept: text/event-stream" $A/ -d '{"jsonrpc":"2.0","id":5,"method":"SendStreamingMessage","params":{
  "message":{"messageId":"'$(uuidgen)'","role":"ROLE_USER","parts":[{"text":"Write hello.py"}]}}}'

# cancel it
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":6,"method":"CancelTask","params":{"id":"<task id>"}}'

# this conversation's tasks
curl -s "${H[@]}" $A/ -d '{"jsonrpc":"2.0","id":7,"method":"ListTasks","params":{"contextId":"<context id>","pageSize":10}}'
```
