"""Loopback-only read-only HTTP adapter for the JS dashboard."""

import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.errors import AppError, ValidationError
from src.models import ReportFormat
from src.web_dashboard import encode, filters_from_dict


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/utils.js": ("utils.js", "text/javascript; charset=utf-8"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log query strings, product names or review bodies.

    def send_bytes(self, status, body, content_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, code, message):
        self.send_bytes(code, encode({"error": message}))

    def do_GET(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in hosts or (origin and origin not in {f"http://{h}" for h in hosts}):
            return self.error(403, "로컬 대시보드 주소로 접속하세요.")
        if len(self.path) > 4096:
            return self.error(400, "요청이 너무 깁니다.")
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
            if any(len(v) != 1 for v in query.values()):
                raise ValidationError("중복된 요청 조건입니다.")
            if path in STATIC:
                name, mime = STATIC[path]
                return self.send_bytes(200, (WEB_ROOT / name).read_bytes(), mime)
            if path == "/api/snapshot":
                values = {k: v[0] for k, v in query.items()}
                page = int(values.pop("page", "1"))
                snapshot = self.server.data.create_snapshot(filters_from_dict(values), page)
                return self.send_bytes(200, encode(snapshot.response))
            pieces = path.strip("/").split("/")
            if len(pieces) >= 3 and pieces[:2] in (["api", "chart"], ["api", "report"], ["api", "review"]):
                snapshot = self.server.data.get_snapshot(pieces[2])
                if query:
                    raise ValidationError("이 요청에는 필터를 추가할 수 없습니다.")
                if pieces[1] == "review" and len(pieces) == 4:
                    review = snapshot.reviews.get(int(pieces[3]))
                    if review is None:
                        return self.error(404, "현재 조회 범위에 없는 리뷰입니다.")
                    return self.send_bytes(200, encode(review))
                if pieces[1] == "chart" and len(pieces) == 3:
                    # Matplotlib global state is protected across HTTP threads.
                    with self.server.data.chart_lock:
                        if snapshot.chart is None:
                            from src.visualizer import DashboardVisualizer
                            with tempfile.TemporaryDirectory(prefix="cra-chart-") as directory:
                                artifacts = DashboardVisualizer().generate_dashboard(snapshot.statistics,
                                    Path(directory) / "dashboard.png", font_family="", dpi=140)
                                snapshot.chart = artifacts[0].path.read_bytes()
                    return self.send_bytes(200, snapshot.chart, "image/png")
                if pieces[1] == "report" and len(pieces) == 4 and pieces[3] in ("md", "txt"):
                    from src.reporter import FileReportGenerator
                    fmt = ReportFormat(pieces[3])
                    with tempfile.TemporaryDirectory(prefix="cra-report-") as directory:
                        artifact = FileReportGenerator().generate_report(snapshot.statistics, snapshot.insight,
                            Path(directory) / f"report.{fmt.value}", report_format=fmt)
                        payload = artifact.path.read_bytes()
                    return self.send_bytes(200, payload, "text/plain; charset=utf-8", f"review-report.{fmt.value}")
            self.error(404, "요청한 항목을 찾을 수 없습니다.")
        except KeyError:
            self.error(410, "조회 결과가 만료됐습니다. 새로고침 후 다시 시도하세요.")
        except (ValueError, ValidationError):
            self.error(400, "조회 조건이나 인사이트 파일을 확인하세요.")
        except (AppError, OSError, ImportError):
            self.error(503, "자료를 불러오지 못했습니다. DB·인사이트 파일·Python 의존성을 확인하세요.")
        except Exception:
            self.error(500, "대시보드 처리 중 오류가 발생했습니다.")

    def do_POST(self):
        self.error(405, "이 대시보드는 조회만 지원합니다.")


def create_server(data, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.daemon_threads = True
    server.data = data
    return server
