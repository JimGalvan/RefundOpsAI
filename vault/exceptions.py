class CredentialError(Exception):
    """Base class for errors whose messages contain no secret values."""


class CredentialConfigurationError(CredentialError):
    pass


class CredentialDomainDeniedError(CredentialError):
    pass


class CredentialInvalidError(CredentialError):
    pass


class CredentialNotFoundError(CredentialError):
    pass
