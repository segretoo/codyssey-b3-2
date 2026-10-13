"""테스트용 가짜 AI API 서버 (로컬 127.0.0.1). 실제 HTTP 요청이 오가는지 검증하는 데 쓴다."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeAIServer:
    def __init__(self, status=200, body=None, delay=0.0, raw_body=None):
        self.requests: list[dict] = []
        self.status, self.delay, self.raw_body = status, delay, raw_body
        self.body = body
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 테스트 출력 조용히
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "json": payload})
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.raw_body is not None:
                    data = outer.raw_body.encode()
                else:
                    data = json.dumps(outer.body if outer.body is not None else outer._default(self.path)).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except ConnectionError:  # BrokenPipe / Reset / Aborted(Windows) 모두 포함
                    pass

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    reply_text = "feat: 테스트 응답\n\n- main.py 수정"

    def _default(self, path):
        if path.endswith("/messages"):
            return {"content": [{"type": "text", "text": self.reply_text}]}
        return {"choices": [{"message": {"content": self.reply_text}}]}

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
