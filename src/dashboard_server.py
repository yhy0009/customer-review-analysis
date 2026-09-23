"""Loopback-only read-only HTTP adapter for the JS dashboard."""

import json
import hmac
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from src.errors import AppError, ValidationError
from src.models import ExportFormat, ReportFormat
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

    def local_request(self, *, mutation=False):
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host, origin = self.headers.get("Host"), self.headers.get("Origin")
        return host in hosts and (origin == f"http://{host}" if mutation
                                  else not origin or origin in {f"http://{h}" for h in hosts})

    def do_GET(self):
        if not self.local_request():
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
            if len(pieces) == 3 and pieces[:2] == ["api", "insight-jobs"] and self.server.data.jobs:
                return self.send_bytes(200, encode(self.server.data.jobs.get(pieces[2])))
            if len(pieces) >= 3 and pieces[:2] in (["api", "chart"], ["api", "report"], ["api", "review"], ["api", "export"]):
                snapshot = self.server.data.get_snapshot(pieces[2])
                if query:
                    raise ValidationError("이 요청에는 필터를 추가할 수 없습니다.")
                if pieces[1] == "export" and len(pieces) == 4 and pieces[3] in ("csv", "jsonl"):
                    if snapshot.export_reviews is None:
                        return self.error(413, "다운로드 건수 한도를 초과했습니다. 필터로 범위를 줄여 다시 조회하세요.")
                    from src.exporter import FileReviewExporter
                    fmt = ExportFormat(pieces[3])
                    with tempfile.TemporaryDirectory(prefix="cra-web-export-") as directory:
                        result = FileReviewExporter().export_reviews(snapshot.export_reviews,
                            Path(directory) / f"reviews.{fmt.value}", export_format=fmt)
                        payload = result.artifact.path.read_bytes()
                    mime = "text/csv; charset=utf-8" if fmt is ExportFormat.CSV else "application/x-ndjson; charset=utf-8"
                    return self.send_bytes(200, payload, mime, f"reviews.{fmt.value}")
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
        from src.dashboard_insights import GenerationConflict
        data = self.server.data
        if self.path != "/api/insight-jobs" or not data.jobs or not data.jobs.factory:
            return self.error(405, "인사이트 생성이 활성화되지 않았습니다.")
        if not self.local_request(mutation=True) or not hmac.compare_digest(
                self.headers.get("X-Dashboard-Token", "").encode(), data.generation_token.encode()):
            return self.error(403, "현재 대시보드 화면에서 요청하세요.")
        if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
            return self.error(400, "JSON 요청이 필요합니다.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError
            self.connection.settimeout(5)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {"snapshot_id"} or not isinstance(payload["snapshot_id"], str):
                raise ValueError
            job = data.start_generation(payload["snapshot_id"])
            self.send_bytes(202 if job["status"] == "running" else 200, encode(job))
        except KeyError:
            self.error(410, "조회 결과가 만료됐습니다. 새로고침 후 생성하세요.")
        except GenerationConflict as exc:
            self.error(409, str(exc))
        except (ValueError, ValidationError):
            self.error(400, "생성 대상이나 요청 형식을 확인하세요.")
        except (OSError, AppError):
            self.error(503, "생성 대상을 불러오지 못했습니다. DB와 연결을 확인하세요.")
        except Exception:
            self.error(500, "인사이트 생성 요청을 처리하지 못했습니다.")


def create_server(data, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.daemon_threads = True
    server.data = data
    return server
