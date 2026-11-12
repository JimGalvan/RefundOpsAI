"""Small synchronous client for the PayPal sandbox REST API."""

from .client import PayPalClient
from .errors import PayPalError

__all__ = ["PayPalClient", "PayPalError"]
