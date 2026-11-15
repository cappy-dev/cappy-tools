import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "llm-bench-lite"))
import llm_bench_lite


class ChunkedResponse:
    def __init__(self, body):
        self.body = body
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.read1(size)

    def read1(self, size):
        chunk = self.body[self.offset:self.offset + min(size, 7)]
        self.offset += len(chunk)
        return chunk


class StreamingUtf8Tests(unittest.TestCase):
    def test_streamed_unicode_survives_chunk_boundaries(self):
        content = "café 🧢"
        event = {"choices": [{"delta": {"content": content}}]}
        body = f"data: {json.dumps(event, ensure_ascii=False)}\n\ndata: [DONE]\n".encode()
        response = ChunkedResponse(body)

        with patch.object(llm_bench_lite.urllib.request, "urlopen", return_value=response):
            result = llm_bench_lite.chat_completion(
                "http://localhost/v1", "", "test-model", "prompt", 10, 0
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["response_preview"], content)

    def test_first_sse_token_is_processed_before_server_finishes(self):
        first_event = (
            b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        )
        final_event = b"data: [DONE]\n\n"
        pause = 1.2
        first_chunk_sent = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(first_event) + len(final_event)))
                self.end_headers()
                self.wfile.write(first_event)
                self.wfile.flush()
                first_chunk_sent.set()
                time.sleep(pause)
                self.wfile.write(final_event)
                self.wfile.flush()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            result = llm_bench_lite.chat_completion(
                f"http://127.0.0.1:{server.server_port}/v1",
                "", "test-model", "prompt", 10, 0
            )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

        self.assertTrue(first_chunk_sent.is_set())
        self.assertTrue(result["success"], result)
        self.assertEqual(result["response_preview"], "first")
        self.assertGreaterEqual(result["total_time"], pause)
        self.assertLess(result["ttft"], pause / 2)


if __name__ == "__main__":
    unittest.main()
