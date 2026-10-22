"""Local permanent access for this owner's free distribution. No identity collection."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FreeStatus:
    active: bool = True
    is_trial: bool = False
    days_left: int = -1
    plan: str = '免费开源'
    expires_at: str = ''
    message: str = '免费开源版，无需激活'


class FreeAccess:
    def __init__(self, **kwargs):
        pass

    def status(self):
        return FreeStatus()

    def ensure_runtime_authorization(self):
        return self.status()

    def current_license_key(self):
        return ''
