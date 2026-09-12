"""ContainerSafe-specific glue around FireAuth (github.com/Fires04/FireAuth)
— the session/cookie mechanism and (optionally) the OIDC client live
there, shared with other apps in this workflow. This module only holds
what's actually specific to this app: the password check and the two
config-driven instances.
"""

import hmac

from fireauth import SessionAuth

from . import config

session = SessionAuth(
    secret=config.SESSION_SECRET,
    cookie_name="cs_session",
    https_only=config.COOKIE_HTTPS_ONLY,
)

oidc = None
if config.OIDC_ENABLED:
    from fireauth.oidc import OIDCClient, OIDCConfig

    oidc = OIDCClient(
        OIDCConfig(
            client_id=config.OIDC_CLIENT_ID,
            client_secret=config.OIDC_CLIENT_SECRET,
            issuer=config.OIDC_ISSUER,
            redirect_uri=config.OIDC_REDIRECT_URI,
        )
    )


def check_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, config.APP_USERNAME) and hmac.compare_digest(
        password, config.APP_PASSWORD
    )


# Thin re-exports so route modules can call auth.is_logged_in /
# auth.require_login_api without reaching into fireauth directly.
is_logged_in = session.is_logged_in
require_login_api = session.require_login
