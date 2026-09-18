"""Clean stored originals and persist each outcome; the caller owns the DB."""

from dataclasses import replace

from src.errors import RawReviewChangedError
from src.models import CleanBatchResult, CleanRequest, DuplicatePolicy, ItemError
from src.services import ReviewCleaner
from src.storage import ReviewRepository


class CleanService:
    """Skip existing Clean rows or revalidate all Raw rows, including rejections.

    Each item is persisted before continuing. Infrastructure failures propagate;
    earlier committed items remain available for a subsequent retry.
    """

    def __init__(self, repository: ReviewRepository, cleaner: ReviewCleaner) -> None:
        self.repository = repository
        self.cleaner = cleaner

    def clean_reviews(self, request: CleanRequest) -> CleanBatchResult:
        originals = self.repository.fetch_raw_reviews()
        existing_ids = (
            {review.id for review in self.repository.fetch_clean_reviews()}
            if request.options.policy is DuplicatePolicy.SKIP else set()
        )
        reviews = []
        errors = []
        skipped = failed = rejected = 0
        for raw in originals:
            # Skip before validation so stricter options cannot remove previously
            # accepted/analyzed reviews unless the caller requests upsert.
            if raw.id in existing_ids:
                skipped += 1
                continue

            # A single-item call distinguishes rejection from cleaning failure
            # and identifies stored successes without changing the shared DTOs.
            cleaned = self.cleaner.clean_reviews([raw], request.options)
            saved = None
            try:
                if cleaned.rejected:
                    self.repository.mark_cleaning_rejected(raw.id, expected_raw=raw)
                if cleaned.reviews:
                    saved = self.repository.save_clean_reviews(
                        cleaned.reviews, request.options.policy, expected_raw=[raw],
                    )
            except RawReviewChangedError:
                failed += 1
                errors.append(ItemError(
                    item_ref=str(raw.id), code="RAW_REVIEW_CHANGED",
                    message="정제 중 원본이 변경되었습니다. clean 명령을 다시 실행하세요.",
                    retryable=True,
                ))
                continue
            rejected += cleaned.rejected
            failed += cleaned.failed
            skipped += cleaned.skipped
            errors.extend(replace(error, item_ref=str(raw.id)) for error in cleaned.errors)
            if saved is None:
                continue

            failed += saved.failed
            skipped += saved.skipped
            errors.extend(replace(error, item_ref=str(raw.id)) for error in saved.errors)
            if saved.succeeded:
                reviews.extend(cleaned.reviews)

        return CleanBatchResult(
            processed=len(originals), succeeded=len(reviews), skipped=skipped,
            failed=failed, rejected=rejected, errors=errors, reviews=reviews,
        )
