"""Resolve an explicit review domain or the source category without guessing from text."""

from __future__ import annotations

import unicodedata

from src.comparison import category_from_payload
from src.models import ReviewDomain


_DOMAIN_KEYS = {"reviewdomain", "domain", "리뷰유형", "대상유형"}
_MOVIE_VALUES = {"movie", "film", "movies", "영화"}
_PRODUCT_VALUES = {"product", "products", "제품", "상품"}


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def review_domain_from_payload(payload: object) -> ReviewDomain:
    if not isinstance(payload, dict):
        raise ValueError("Review metadata must be an object")
    declared = set()
    for key, value in payload.items():
        normalized_key = _normalized(key).replace(" ", "").replace("_", "").replace("-", "")
        if normalized_key not in _DOMAIN_KEYS or value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ValueError("Review domain must be a string")
        domain = _normalized(value)
        if domain in _MOVIE_VALUES:
            declared.add(ReviewDomain.MOVIE)
        elif domain in _PRODUCT_VALUES:
            declared.add(ReviewDomain.PRODUCT)
        else:
            raise ValueError("Unsupported review domain")
    if len(declared) > 1:
        raise ValueError("Conflicting review domains")
    category = category_from_payload(payload)
    category_is_movie = category is not None and _normalized(category) in _MOVIE_VALUES
    if category_is_movie and declared == {ReviewDomain.PRODUCT}:
        raise ValueError("Movie category conflicts with product domain")
    if declared:
        return next(iter(declared))
    return ReviewDomain.MOVIE if category_is_movie else ReviewDomain.PRODUCT
