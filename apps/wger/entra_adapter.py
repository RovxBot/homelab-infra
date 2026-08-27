"""Entra-specific allauth behavior for this Wger deployment."""

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class EntraSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Allow new accounts only when they arrive through the configured OIDC provider."""

    def is_open_for_signup(self, request, sociallogin):
        return sociallogin.account.provider == "entra"
