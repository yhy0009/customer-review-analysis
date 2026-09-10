"""The analyze use case; repository ownership stays with the caller."""

from src.errors import ValidationError
from src.models import AnalysisBatchResult, AnalysisOptions, AnalyzeRequest, AnalyzeTarget
from src.services import ReviewAnalyzer
from src.storage import ReviewRepository


class AnalysisService:
    """Select CLI-requested targets and delegate skip/retry/save to the analyzer."""

    def __init__(
        self, repository: ReviewRepository, analyzer: ReviewAnalyzer, options: AnalysisOptions,
    ) -> None:
        self.repository = repository
        self.analyzer = analyzer
        self.options = options

    def analyze_reviews(self, request: AnalyzeRequest) -> AnalysisBatchResult:
        if request.target is AnalyzeTarget.REVIEW_ID:
            detail = self.repository.get_review(request.review_id)
            if detail is None:
                raise ValidationError("분석 대상 정제 리뷰를 찾을 수 없습니다.")
            reviews = [detail.review]
        elif request.target is AnalyzeTarget.UNANALYZED:
            # Includes previous failures; force does not broaden the selected set.
            reviews = self.repository.fetch_unanalyzed_reviews(limit=request.limit)
        else:
            reviews = self.repository.fetch_clean_reviews(limit=request.limit)
        return self.analyzer.analyze_reviews(reviews, self.options, force=request.force)
