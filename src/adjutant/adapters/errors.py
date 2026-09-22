"""Safe provider rejection details retained for reconciliation and operator review."""

from adjutant.errors import DomainError


class ProviderRejection(DomainError):
    """Retain the provider's exact message after removing credential material."""

    def __init__(self, code: str, provider_code: str, raw_message: str, provider: str) -> None:
        super().__init__(code, f"{provider} {provider_code}: {raw_message}", 502)
        self.provider_code = provider_code
        self.raw_message = raw_message
