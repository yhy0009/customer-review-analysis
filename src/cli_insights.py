"""Persist CLI extraction snapshots and reuse them only against matching sources."""

import hashlib
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path

from src.dashboard_insights import matches
from src.errors import OutputError, ValidationError
from src.insight_provenance import validate_profile
from src.web_dashboard import encode, load_insight_artifact, make_insight_artifact, select_analyzed


class CLIInsightStore:
    def __init__(self, database, output_directory, profile):
        self.database = Path(database).resolve()
        namespace = hashlib.sha256(str(self.database).encode()).hexdigest()
        self.directory = Path(output_directory) / "insights" / namespace
        self.profile = validate_profile(profile)
        self.saved_path = None

    def path_for(self, filters, limit):
        key = hashlib.sha256(encode([asdict(filters), limit])).hexdigest()
        return self.directory / (key + ".json")

    def _check_target(self, path):
        protected = [self.database] + [self.database.with_name(self.database.name + suffix)
                                      for suffix in ("-wal", "-shm", "-journal")]
        if (path.is_symlink() or (path.exists() and not path.is_file())
                or path.resolve() in protected
                or (path.exists() and path.samefile(self.database))):
            raise OutputError("인사이트는 SQLite 저장소와 겹치지 않는 일반 파일에 저장해야 합니다.")

    def prepare(self, filters, limit):
        """Check destination writability before spending an AI request."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._check_target(self.path_for(filters, limit))
            with tempfile.TemporaryFile(dir=self.directory):
                pass
        except OSError as exc:
            raise OutputError("인사이트 저장 경로·권한을 확인하세요.") from exc

    def save(self, details, result, limit):
        artifact = make_insight_artifact(details, result, limit, profile=self.profile)
        target = self.path_for(result.filters, limit)
        temporary = None
        try:
            self.prepare(result.filters, limit)
            fd, name = tempfile.mkstemp(prefix=".insight-", suffix=".tmp", dir=self.directory)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encode(artifact))
                stream.flush()
                os.fsync(stream.fileno())
            checked = load_insight_artifact(temporary)
            if not matches(checked, details, result.filters):
                raise ValidationError("인사이트가 추출 대상 리뷰의 원문과 일치하지 않습니다.")
            self._check_target(target)
            os.replace(temporary, target)
            self.saved_path = target
            return target
        except OSError as exc:
            raise OutputError("인사이트 파일을 저장할 수 없습니다. 저장 경로·공간을 확인하세요.") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    @staticmethod
    def _scope_matches(candidate, requested):
        # A sentiment-specific insight can accompany broader statistics, with its
        # own scope/count displayed. Product/date/rating conditions must match.
        return (replace(candidate, sentiment=requested.sentiment) == requested
                and (requested.sentiment is None or candidate.sentiment == requested.sentiment))

    def load(self, repository, request):
        """Caller holds a read snapshot shared with report statistics."""
        if not request.use_insights:
            return None, "disabled"
        explicit = request.insight_file is not None
        candidates = [request.insight_file] if explicit else sorted(self.directory.glob("*.json"))
        selected = []
        status = "missing"
        reasons = {
            "invalid": "저장 파일 형식이나 원문 근거가 올바르지 않습니다.",
            "scope_mismatch": "제품·기간·별점·감정 조건이 대시보드와 일치하지 않습니다.",
            "config_mismatch": "모델·프롬프트·AI 서버 설정이 변경됐습니다.",
            "stale": "추출 이후 리뷰 또는 분석 결과가 변경됐습니다.",
        }
        for path in candidates:
            try:
                artifact = load_insight_artifact(path)
                result = artifact["result"]
                if not explicit and path != self.path_for(result.filters, artifact["selection_limit"]):
                    reason = "invalid"
                elif not self._scope_matches(result.filters, request.filters):
                    reason = "scope_mismatch"
                elif artifact.get("generation_profile") != self.profile:
                    reason = "config_mismatch"
                else:
                    details = select_analyzed(repository, result.filters, artifact["selection_limit"])
                    reason = "available" if matches(artifact, details, result.filters) else "stale"
            except ValidationError:
                reason = "invalid"
            if reason == "available":
                selected.append((result.generated_at, path.name, result))
            elif explicit:
                raise ValidationError("지정한 인사이트를 사용할 수 없습니다. " + reasons[reason]
                                      + " 같은 조건으로 extract를 다시 실행하세요.")
            elif status in ("missing", "scope_mismatch") or reason != "scope_mismatch":
                status = reason
        if selected:
            return max(selected, key=lambda item: item[:2])[2], "available"
        return None, status
