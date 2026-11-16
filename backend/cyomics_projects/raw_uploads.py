"""Dedicated bounded ASGI raw-file boundary; disabled unless explicitly opted in."""
import asyncio
import math
import time
from urllib.parse import quote
from starlette.requests import Request
from starlette.responses import JSONResponse
from .identity import jwt_payload
from .settings import Fault, consume_task
from .upload_repository import UploadRepository, UploadOutcome
from .upload_validation import MAX_BYTES, classify, owns, reject_route, input_headers, check_content

SECURITY = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}
ERRORS = {
    400: {'invalid_upload','upload_checksum_mismatch','invalid_upload_query'},
    401: {'unauthenticated'}, 403: {'auth_origin_rejected'},
    404: {'project_not_found','file_not_found'}, 405: {'method_not_allowed'},
    409: {'project_context_changed','upload_request_conflict'},
    413: {'upload_too_large','upload_quota_exceeded'}, 415: {'unsupported_media_type'},
    429: {'upload_rate_limited','upload_busy'}, 503: {'uploads_unavailable'},
    504: {'upload_result_unknown'},
}

class ClientGone(Exception):
    pass

class RawUploads:
    def __init__(self, authority, pool, budget_factory, enabled=False):
        self.authority, self.repository, self.budget_factory = authority, UploadRepository(pool), budget_factory
        self.enabled = enabled is True
        self.long_active = 0
        self.subject_active = set()
        self.attempts = {}

    owns = staticmethod(owns)

    async def __call__(self, scope, receive, send):
        async def error(fault):
            # Shared legacy helpers cannot expand the four raw routes' contract.
            if fault.code not in ERRORS.get(fault.status,set()):
                fault = Fault(503,'uploads_unavailable')
            await JSONResponse({'error':{'code':fault.code}}, status_code=fault.status, headers=SECURITY)(scope, receive, send)
        if not self.enabled:
            return await error(Fault(503, 'uploads_unavailable'))
        route = classify(scope)
        try:
            if route is None:
                reject_route(scope)
            budget = self.budget_factory(scope)
        except Fault as fault:
            if (fault.status,fault.code) == (400,'invalid_project_query'):
                fault = Fault(400,'invalid_upload_query')
            return await error(fault)
        except Exception:
            return await error(Fault(503, 'uploads_unavailable'))
        budget.create = UploadOutcome()
        scope['cyomics.budget'] = budget
        if route['long'] and self.long_active >= 2:
            return await error(Fault(429,'upload_busy'))
        if route['long']:
            self.long_active += 1
        subject = None
        admitted = False
        ready = asyncio.get_running_loop().create_future()
        sent = False

        async def pump():
            chunks = bytearray()
            cap = MAX_BYTES if route['kind'] == 'save' else 0
            while True:
                budget.check()
                event = await receive()
                if event['type'] == 'http.disconnect':
                    raise ClientGone()
                if event['type'] != 'http.request' or ready.done():
                    raise Fault(400,'invalid_upload')
                data = event.get('body',b'')
                if not isinstance(data, bytes):
                    raise Fault(400,'invalid_upload')
                if len(chunks)+len(data) > cap:
                    raise Fault(413 if cap else 400,'upload_too_large' if cap else 'invalid_upload_query')
                chunks.extend(data)
                if not event.get('more_body',False):
                    ready.set_result(bytes(chunks))
                    # Continue watching disconnect without retaining another content copy.
                    chunks.clear()
                    while True:
                        later = await receive()
                        if later['type'] == 'http.disconnect':
                            raise ClientGone()
                        raise Fault(400,'invalid_upload')

        async def work():
            nonlocal subject, admitted
            request = Request(scope)
            values = input_headers(request) if route['kind']=='save' else None
            subject = await self.authority.verify(request,budget)
            # Authority has already authenticated these claims; never used as a substitute.
            token = request.headers.getlist('authorization')[0][7:]
            expiry = jwt_payload(token)['exp']
            left = expiry-time.time()
            if not math.isfinite(left) or left <= 0:
                raise Fault(401,'unauthenticated')
            budget.deadline = min(budget.deadline,time.monotonic()+left)
            budget.check()
            if route['kind']=='save':
                now = time.monotonic()
                self.attempts = {k:v for k,v in self.attempts.items() if v[0]>now}
                if subject in self.subject_active:
                    raise Fault(429,'upload_busy')
                until,count = self.attempts.get(subject,(now+600,0))
                if count >= 10:
                    raise Fault(429,'upload_rate_limited')
                if subject not in self.attempts and len(self.attempts)>=256:
                    raise Fault(429,'upload_busy')
                self.attempts[subject] = (until,count+1)
                self.subject_active.add(subject); admitted = True
                await self.repository.ensure_project(subject,route['project_id'],budget)
            raw = await ready
            budget.check()
            if route['kind']=='save':
                check_content(raw,values)
                code,value = await self.repository.save(subject,route['project_id'],values,raw,budget)
            else:
                code = 200
                value = await self.repository.read(subject,route,budget)
            budget.check()
            if route['kind']=='content':
                file,content = value
                headers = {**SECURITY,'Content-Type':'application/octet-stream','Content-Length':str(file['bytes']),
                           'Content-Disposition':"attachment; filename=\"proteinGroups.txt\"; filename*=UTF-8''"+quote(file['filename'],safe=''),
                           'X-Cyomics-File-Sha256':file['sha256']}
                return code,headers,content
            response = JSONResponse(value,status_code=code,headers=SECURITY)
            if len(response.body)>16384:
                raise Fault(503,'uploads_unavailable')
            return code,dict(response.headers),response.body

        watcher,worker = asyncio.create_task(pump()),asyncio.create_task(work())
        try:
            done,_ = await budget.run(lambda:asyncio.wait({watcher,worker},return_when=asyncio.FIRST_COMPLETED))
            if watcher in done:
                await watcher
            code,headers,body = await worker
            if len(body)>(MAX_BYTES if route['kind']=='content' else 16384):
                raise Fault(503,'uploads_unavailable')
            if watcher.done():
                await watcher
            await budget.run(lambda:send({'type':'http.response.start','status':code,'headers':[(k.lower().encode('ascii'),v.encode('latin1')) for k,v in headers.items()]}))
            sent = True
            for offset in range(0,max(1,len(body)),65536):
                if watcher.done():
                    await watcher
                part = body[offset:offset+65536]
                await budget.run(lambda:send({'type':'http.response.body','body':part,'more_body':offset+len(part)<len(body)}))
        except ClientGone:
            pass
        except asyncio.CancelledError:
            raise
        except Fault as fault:
            if not sent:
                await error(fault)
        except Exception:
            if not sent:
                await error(budget.create.failure())
        finally:
            for task in (watcher,worker):
                if not task.done():
                    task.cancel()
                task.add_done_callback(consume_task)
            if admitted:
                self.subject_active.discard(subject)
            if route['long']:
                self.long_active -= 1
            await asyncio.sleep(0)
