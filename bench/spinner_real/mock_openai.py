#!/usr/bin/env python3
"""OpenAI-compatible chat completions server that holds a Zed agent thread in one state.

Usage: mock_openai.py <port> <mode> <marker-file>

Modes, for requests that offer tools (agent turns):
  hang       Send response headers, then nothing: the thread waits for the model.
  confirm    Stream one `terminal` tool call and finish: the thread awaits confirmation.
  done       Answer and finish: nothing animates afterward.
  stream     Stream a word every 33 ms until the client disconnects.
  subagent   Spawn one subagent, whose own turn hangs.
  subagents  Spawn three subagents 350 ms apart, whose turns hang.
Other requests (thread titles) get a one-word answer.

Once the thread reaches its state, a line is appended to <marker-file>.
"""
import itertools, json, socketserver, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port, mode, marker = int(sys.argv[1]), sys.argv[2], sys.argv[3]
SUBAGENTS = {"subagent": 1, "subagents": 3}.get(mode, 0)
WORDS = ("The quick brown fox jumps over the lazy dog while the spinner keeps "
         "turning and the thread view renders each new chunk of markdown text").split()
lock = threading.Lock()
subagent_turns = 0


def mark(note):
    with open(marker, "a") as file:
        file.write(f"{time.time()} {mode} {note}\n")


def chunk(delta, finish_reason=None):
    body = {
        "id": "chatcmpl-bench",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "bench-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


def tool_call(index, name, arguments):
    return chunk({"role": "assistant", "content": None, "tool_calls": [
        {"index": index, "id": f"call_bench_{index}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}]})


def user_text(request):
    texts = []
    for message in request.get("messages", []):
        if message.get("role") == "user":
            texts.append(json.dumps(message.get("content")))
    return " ".join(texts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        sys.stderr.write("mock: " + (format % args) + "\n")

    def send(self, *chunks):
        for data in chunks:
            self.wfile.write(data)
        self.wfile.flush()

    def finish_stream(self, reason):
        self.send(chunk({}, reason), b"data: [DONE]\n\n")

    def hang(self):
        while True:
            time.sleep(3600)

    def do_POST(self):
        global subagent_turns
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        agent_turn = bool(request.get("tools"))
        is_subagent = "bench-subtask" in user_text(request)
        sys.stderr.write(f"mock: POST {self.path} tools={len(request.get('tools') or [])} subagent={is_subagent}\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        if not agent_turn:
            self.send(chunk({"role": "assistant", "content": "Bench"}))
            self.finish_stream("stop")
            return
        if SUBAGENTS:
            if is_subagent:
                with lock:
                    subagent_turns += 1
                    if subagent_turns == SUBAGENTS:
                        mark("subagents running")
                self.hang()
            with lock:
                subagent_turns = 0
            for index in range(SUBAGENTS):
                if index:
                    time.sleep(0.35)
                self.send(tool_call(index, "spawn_agent", {
                    "label": f"Subtask {index + 1}", "message": f"bench-subtask {index + 1}"}))
            self.finish_stream("tool_calls")
            return
        if mode == "hang":
            mark("waiting for the model")
            self.hang()
        if mode == "done":
            self.send(chunk({"role": "assistant", "content": "Done."}))
            self.finish_stream("stop")
            mark("done")
            return
        if mode == "confirm":
            self.send(tool_call(0, "terminal", {"command": "true", "cd": "."}))
            self.finish_stream("tool_calls")
            mark("awaiting confirmation")
            return
        if mode == "stream":
            self.send(chunk({"role": "assistant", "content": ""}))
            mark("streaming")
            try:
                for count, word in enumerate(itertools.cycle(WORDS), 1):
                    separator = "\n\n" if count % 40 == 0 else " "
                    self.send(chunk({"content": word + separator}))
                    time.sleep(1 / 30)
            except (BrokenPipeError, ConnectionResetError):
                return
        raise SystemExit(f"unknown mode {mode}")


class Server(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer resolves the host's name here, which makes macOS ask for
        # local network access and stalls the stream until someone answers.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


server = Server(("127.0.0.1", port), Handler)
server.daemon_threads = True
sys.stderr.write(f"mock: listening on {port} mode={mode}\n")
server.serve_forever()
