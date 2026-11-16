from datetime import datetime, timezone
import math
import re
import time
import httpx
from .settings import AUTH_USER_URL, EXPECTED_AUDIENCE, EXPECTED_ISSUER, MAX_TOKEN_LIFETIME, Fault, unavailable
from .validation import canonical_uuid, decode_url64, strict_json


def unauthorized():
    return Fault(401, 'unauthenticated')


def jwt_payload(token):
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError()
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError()
    header = strict_json(decode_url64(parts[0], 2048))
    payload = strict_json(decode_url64(parts[1], 12288))
    if type(header) is not dict or not isinstance(header.get('alg'), str) or header['alg'].lower() == 'none':
        raise ValueError()
    decode_url64(parts[2], 4096)
    if type(payload) is not dict:
        raise ValueError()
    return payload


class Authority:
    """Every admitted request calls fixed /user. No cached positive identity."""
    def __init__(self, transport, anon_key_loader):
        if transport is None:
            transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False, timeout=3)
        self._anon_key_loader = anon_key_loader

    async def close(self):
        await self._client.aclose()

    async def verify(self, request, budget):
        budget.check()
        headers = request.headers.getlist('authorization')
        if len(headers) != 1 or not headers[0].startswith('Bearer ') or len(headers[0]) > 16400:
            raise unauthorized()
        token = headers[0][7:]
        if not token or re.fullmatch(r'[A-Za-z0-9_.-]+', token) is None:
            raise unauthorized()
        try:
            key = self._anon_key_loader()
            if jwt_payload(key).get('role') != 'anon':
                raise ValueError()
        except Exception:
            raise unavailable() from None

        async def authoritative_user():
            async with self._client.stream('GET', AUTH_USER_URL, headers={'Authorization':'Bearer ' + token,'apikey':key}) as response:
                if response.status_code in (400,401,403):
                    raise unauthorized()
                if response.status_code != 200:
                    raise unavailable()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise unavailable()
                try:
                    return strict_json(bytes(raw))
                except (ValueError, UnicodeError, RecursionError):
                    raise unauthorized() from None
        try:
            user = await budget.run(authoritative_user, 3)
        except Fault:
            raise
        except Exception:
            raise unavailable() from None
        # Payload parsing happens ONLY AFTER authoritative success. This is
        # supplemental policy checking, never local signature verification.
        try:
            claims = jwt_payload(token)
            now = time.time()
            if type(user) is not dict or not canonical_uuid(user.get('id')):
                raise ValueError()
            if claims.get('sub') != user['id'] or claims.get('iss') != EXPECTED_ISSUER:
                raise ValueError()
            if claims.get('aud') not in (EXPECTED_AUDIENCE, [EXPECTED_AUDIENCE]):
                raise ValueError()
            if user.get('aud') != EXPECTED_AUDIENCE or user.get('role') != 'authenticated' or claims.get('role') != 'authenticated':
                raise ValueError()
            if user.get('is_anonymous') is not False or claims.get('is_anonymous', False) is not False:
                raise ValueError()
            exp, iat = claims.get('exp'), claims.get('iat')
            if type(exp) not in (int,float) or type(iat) not in (int,float) or not math.isfinite(exp) or not math.isfinite(iat):
                raise ValueError()
            if not (0 < iat <= now < exp and 0 < exp-iat <= MAX_TOKEN_LIFETIME):
                raise ValueError()
            email = user.get('email')
            if not isinstance(email,str) or not 1 <= len(email) <= 320 or '@' not in email or email != email.strip() or any(ord(c) < 33 or ord(c) == 127 for c in email):
                raise ValueError()
            confirmed = datetime.fromisoformat(user['email_confirmed_at'].replace('Z','+00:00'))
            if confirmed.tzinfo is None or not math.isfinite(confirmed.timestamp()) or confirmed.astimezone(timezone.utc).timestamp() > now:
                raise ValueError()
            return user['id']
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):
            raise unauthorized() from None
