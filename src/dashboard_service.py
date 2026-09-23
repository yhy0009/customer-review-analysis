"""Generate chart and report files from one stored statistics result, without AI."""
from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.errors import OutputError
from src.models import DashboardRequest, DashboardResult, OutputArtifact
from src.services import ReportGenerator, ReviewVisualizer
from src.sentiment_alerts import detect_sentiment_change
from src.storage import ReviewRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DashboardService:
    """Borrow the repository; rendering and connection ownership stay outside it.

    Both generators finish in a temporary directory before publishing any file.
    Publication is atomic per file, not a transaction spanning both artifacts.
    """

    def __init__(
        self, repository: ReviewRepository, visualizer: ReviewVisualizer,
        reporter: ReportGenerator, *, font_family: str = "", dpi: int = 150,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.repository = repository
        self.visualizer = visualizer
        self.reporter = reporter
        self.font_family = font_family
        self.dpi = dpi
        self._clock = clock

    def create_dashboard(self, request: DashboardRequest) -> DashboardResult:
        # SQLite aggregates these statistics from a single SELECT. Every output
        # consumes the same result, including when other commands update the DB.
        now = self._clock().astimezone(timezone.utc)
        statistics = self.repository.get_statistics(request.filters)
        sentiment_change = (detect_sentiment_change(
            statistics, request.filters, request.alert_options, today=now.date(),
        ) if request.alert_options is not None else None)
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        chart_name = f"dashboard_{timestamp}.png"
        report_name = f"report_{timestamp}.{request.report_format.value}"
        published: list[OutputArtifact] = []
        try:
            # The CLI's output is always a directory, even if it ends in .png.
            directory = request.output.resolve()
            directory.mkdir(parents=True, exist_ok=True)
            for name in (chart_name, report_name):
                self._check_target(directory / name, force=request.force)
            with tempfile.TemporaryDirectory(prefix=".dashboard-", dir=directory) as temporary:
                stage = Path(temporary)
                charts = self.visualizer.generate_dashboard(
                    statistics, stage / chart_name,
                    font_family=self.font_family, dpi=self.dpi, force=False,
                )
                report = self.reporter.generate_report(
                    statistics, None, stage / report_name,
                    report_format=request.report_format, force=False,
                )
                artifacts = [*charts, report]
                # Only complete files created inside our staging directory may
                # be published. This also catches an incomplete injected module.
                names = set()
                for artifact in artifacts:
                    source = artifact.path
                    if (source.parent != stage or source.is_symlink()
                            or not source.is_file() or source.name in names):
                        raise OutputError("대시보드 생성기가 올바른 산출물 파일을 반환하지 않았습니다.")
                    names.add(source.name)
                    self._check_target(directory / source.name, force=request.force)
                for artifact in artifacts:
                    target = directory / artifact.path.name
                    if request.force:
                        os.replace(artifact.path, target)
                    else:
                        # No overwrite even if another process creates the name
                        # after preflight. Stage and destination share a volume.
                        os.link(artifact.path, target)
                    published.append(replace(artifact, path=target))
        except OSError as exc:
            message = "대시보드 파일을 저장할 수 없습니다. 출력 경로·권한·기존 파일을 확인하세요."
            if published:
                message += " 이미 저장된 파일: " + ", ".join(str(a.path) for a in published)
            raise OutputError(message) from exc
        return DashboardResult(artifacts=published, statistics=statistics, insight=None,
                               sentiment_change=sentiment_change)

    @staticmethod
    def _check_target(target: Path, *, force: bool) -> None:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise OutputError(f"일반 파일이 아닌 출력 경로는 사용할 수 없습니다: {target}")
        if target.exists() and not force:
            raise OutputError(f"이미 출력 파일이 있습니다. 덮어쓰려면 --force를 사용하세요: {target}")
