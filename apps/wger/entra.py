"""Wger settings overlay for Entra ID-only authentication."""

import os

from .main import *  # noqa: F403

SOCIALACCOUNT_ONLY = True
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"

# allauth disallows SOCIALACCOUNT_ONLY when its local MFA app is installed.
# Entra performs MFA/conditional-access policies before issuing the OIDC token.
INSTALLED_APPS.remove("allauth.mfa")

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
