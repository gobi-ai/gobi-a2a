"""A stand-in for cursor-agent / codex / omp / copilot in the tests.

Prints its working folder and its arguments, a line per word of the prompt, then "answer: <prompt>". The prompt
(argv[1]) steers it: "fail" exits 3 with a message on stderr, "hang" sleeps until killed, "touch" writes a file in its
folder and lists what is there, "slow" logs its begin and end to turns.log, 0.4s apart. "json" prints Codex-style JSON events instead: a session (its id names how many runs
the folder has seen), and its arguments as the answer. "json hang" prints a session and a turn's usage, then sleeps
until killed.
"""
import json
import os
import sys
import time
from pathlib import Path

prompt, rest = sys.argv[1], sys.argv[2:]
if prompt == "json hang":
    for e in [{"type": "thread.started", "thread_id": "session-hang"},
              {"type": "turn.completed", "usage": {"input_tokens": 7, "cached_input_tokens": 3, "output_tokens": 1}}]:
        print(json.dumps(e), flush=True)
    Path("started").write_text(str(os.getpid()))
    time.sleep(60)
if prompt == "json":
    runs = len(list(Path().glob("run-*"))) + 1
    Path(f"run-{runs}").write_text("x")
    if "gone" in rest:
        print("no rollout found for that session", file=sys.stderr, flush=True)
        sys.exit(1)
    events = [{"type": "thread.started", "thread_id": f"session-{runs}"},
              {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}},
              {"type": "item.completed", "item": {"type": "agent_message", "text": f"args: {json.dumps(rest)}"}},
              {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}]
    print("not json, a plain line", flush=True)
    for e in events:
        print(json.dumps(e), flush=True)
    sys.exit(0)
print(f"cwd: {os.getcwd()}", flush=True)
print(f"args: {json.dumps(rest)}", flush=True)
for word in prompt.split():
    print(f"step {word}", flush=True)
if prompt == "fail":
    print("it broke", file=sys.stderr, flush=True)
    sys.exit(3)
if prompt == "hang":
    Path("started").write_text(str(os.getpid()))
    time.sleep(60)
if prompt == "touch":
    Path(f"note-{len(list(Path().iterdir()))}.txt").write_text("x")
    print(f"files: {sorted(p.name for p in Path().iterdir())}", flush=True)
if prompt == "slow":
    with open("turns.log", "a") as f:
        f.write("begin\n")
    time.sleep(0.4)
    with open("turns.log", "a") as f:
        f.write("end\n")
print(f"answer: {prompt}", flush=True)
