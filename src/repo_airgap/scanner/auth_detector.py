import re

AUTH_SIGNALS = re.compile(
    r"accounts\.google\.com|oauth2\.googleapis\.com|authlib|googleprovider|"
    r"next-auth/providers/google|signInWithPopup|auth/google|"
    r"google[-_.]auth[-_.]oauthlib|google\.oauth2|google_auth_oauthlib|"
    r"passport[-_]google[-_]oauth|GoogleStrategy|GoogleAuthProvider|"
    r"@react-oauth/google|react[-_]google[-_]login|flask[-_]dance",
    re.IGNORECASE,
)
