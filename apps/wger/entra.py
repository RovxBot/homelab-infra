"""Wger settings overlay for Entra ID-only authentication."""

import os

from allauth.account.checks import settings_check as allauth_settings_check
from django.core.checks import register
from django.core.checks.registry import registry

from .main import *  # noqa: F403

SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"

# Wger imports allauth.mfa models, so that app must remain installed.  Its
# default check considers this incompatible with SOCIALACCOUNT_ONLY, despite
# Entra being the only enabled login method.  Keep every other allauth check.
registry.unregister(allauth_settings_check)


@register()
def wger_socialaccount_only_settings_check(app_configs, **kwargs):
    return [
        issue
        for issue in allauth_settings_check(app_configs, **kwargs)
        if issue.msg != "SOCIALACCOUNT_ONLY does not work with 'allauth.mfa'"
    ]

SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "entra",
                "name": "Entra ID",
                "client_id": os.environ["ENTRA_CLIENT_ID"],
                "secret": os.environ["ENTRA_CLIENT_SECRET"],
                "settings": {
                    "server_url": (
                        "https://login.microsoftonline.com/"
                        f"{os.environ['ENTRA_TENANT_ID']}/v2.0"
                    ),
                    "fetch_userinfo": False,
                    "oauth_pkce_enabled": True,
                    "token_auth_method": "client_secret_post",
                    "uid_field": "sub",
                },
            },
        ],
    },
}
