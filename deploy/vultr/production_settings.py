"""VM overrides, selected by systemd without modifying application settings."""

from pathlib import Path

from RefundOpsAI.settings import *  # noqa: F403
from RefundOpsAI.settings import DATABASES

DATA_DIR = Path('/var/lib/refundops')
DATABASES = {
    'default': {**DATABASES['default'], 'NAME': DATA_DIR / 'db.sqlite3'},
}
MEDIA_ROOT = DATA_DIR / 'media'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
# Mock sites use loopback HTTP; public CSRF cookies still use Django's defaults.
# Setting CSRF_COOKIE_SECURE globally would break those local forms.
