"""Public generation guidance built only from allowlisted error metadata."""

from src.errors import AIErrorCode, AIProviderError, ConfigError, ValidationError, safe_ai_error_details


def generation_failure(error, *, saving=False):
    code, action = "GENERATION_FAILED", "retry"
    message = "인사이트 생성에 실패했습니다. 잠시 후 다시 시도하세요. 문제가 계속되면 서버 로그를 확인하세요."
    if isinstance(error, AIProviderError):
        code, status = safe_ai_error_details(error)
        if code == AIErrorCode.QUOTA.value:
            action = "check_settings"
            message = "AI 이용 한도가 부족합니다. 제공 서비스의 잔액·할당량을 확인한 뒤 다시 생성하세요."
        elif code == AIErrorCode.RATE_LIMIT.value:
            message = "AI 요청이 일시적으로 제한됐습니다. 잠시 기다린 뒤 다시 생성하세요."
        elif code in {AIErrorCode.TIMEOUT.value, AIErrorCode.CONNECTION.value}:
            message = "AI 서버에 연결하지 못했거나 응답 시간이 초과됐습니다. 연결 상태를 확인한 뒤 다시 생성하세요."
        elif code == AIErrorCode.HTTP.value and status in {400, 401, 403, 404, 422}:
            action = "check_settings"
            message = "AI 요청 설정이나 접근 권한을 확인해야 합니다. 키·모델·서버 설정을 수정했다면 대시보드 서버를 다시 시작하세요."
        elif code in {AIErrorCode.OUTPUT_LIMIT.value, AIErrorCode.REFUSAL.value}:
            action = "change_scope"
            message = "AI가 응답을 완료하지 못했습니다. 제품·기간 필터로 대상 범위를 줄여 다시 생성하세요. 반복되면 모델 설정을 확인하세요."
        elif code.startswith(("EVIDENCE_", "INSIGHT_")):
            message = "AI 결과의 형식이나 원문 근거 검증을 통과하지 못했습니다. 다시 생성하세요. 반복되면 대상 범위를 줄이거나 모델 설정을 확인하세요."
    elif isinstance(error, (ConfigError, ImportError)):
        code, action = "GENERATION_CONFIG", "check_settings"
        message = "AI 설정이나 필요한 패키지를 확인해야 합니다. 설정·의존성을 수정한 뒤 대시보드 서버를 다시 시작하세요."
    elif isinstance(error, OSError) and saving:
        code, action = "INSIGHT_STORAGE", "check_storage"
        message = "생성한 인사이트를 파일로 저장하지 못했습니다. 저장 폴더의 권한·공간을 확인한 뒤 다시 생성하세요."
    elif isinstance(error, ValidationError):
        code = "INSIGHT_VALIDATION"
        message = "생성 결과가 현재 리뷰와 일치하지 않아 저장하지 않았습니다. 다시 생성하세요. 반복되면 서버 로그를 확인하세요."
    return {"error": message, "error_code": code, "retry_action": action}
