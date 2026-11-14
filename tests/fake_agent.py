"""A stand-in for cursor-agent / aider / omp in the tests.

Prints its working folder and its arguments, a line per word of the prompt, then "answer: <prompt>". The prompt
(argv[1]) steers it: "fail" exits 3 with a message on stderr, "hang" sleeps until killed, "touch" writes a file in its
folder and lists what is there.
"""
import json
import os
import sys
import time
from pathlib import Path

prompt, rest = sys.argv[1], sys.argv[2:]
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
print(f"answer: {prompt}", flush=True)
