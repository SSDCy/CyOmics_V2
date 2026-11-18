"""Offline full-app ASGI tests: real Authority with synthetic MockTransport /user.

No real authentication service, DB, file data, worker, R, pool or sockets.
"""
import asyncio
import base64
import copy
from datetime import datetime,timezone
import inspect
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
from uuid import UUID

import httpx
from cyomics_projects import app as app_source,preprocess_http as api,preprocess_repository as p
from cyomics_projects.settings import Budget,Fault,EXPECTED_ISSUER

OWNER='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'
PROJECT='33333333-3333-4333-8333-333333333333'
DATASET='44444444-4444-4444-8444-444444444444'
FILE='55555555-5555-4555-8555-555555555555'
RID='66666666-6666-4666-8666-666666666666'
JOB='77777777-7777-4777-8777-777777777777'
EXECUTION={'contract':'log2_local_source_proposal_v0.1','entry_kind':'native_R','r_version':'R version 4.6.1',
    **copy.deepcopy(p.FIXED_PINS),'runner':{'bytes':21566,'sha256':'b9731437092d34d9143242c4ea3ec68593ca13869b99e910adc30efeb60f5db8'},
    'limits':dict(p.LIMITS),'process_policy_sha256':p.POLICY_SHA256,'registered':False}
SOURCE={'project_id':PROJECT,'dataset_version_id':DATASET,'file_id':FILE,'source_bytes':61,'source_sha256':'a'*64,
    'parser_contract':'protein_groups_txt_v1','config_sha256':'b'*64,'report_sha256':'c'*64,
    'row_count':2,'column_count':3,'selected_columns_sha256':'d'*64}

def body(family='Intensity'):
    return p.canonical({'request_id':RID,'quant_family':family,'parameters':json.loads(
        p.contract.canonical_parameters({'contaminant':False,'reverse':False,'site_only':False}))}).encode()

def dto(raw=None):
    request=p.contract.request_input(raw or body());rh=p.scoped_request_hash(PROJECT,DATASET,request)
    row=[UUID(JOB),UUID(PROJECT),UUID(DATASET),UUID(FILE),UUID(OWNER),UUID(request.request_id),rh,
        p.binding_hash(rh,SOURCE,request,EXECUTION),p.canonical(SOURCE),request.quant_family,
        request.parameters_bytes.decode(),p.canonical(EXECUTION),'queued',datetime(2026,11,18,tzinfo=timezone.utc)]
    return p.PreprocessRepository()._decode(row,OWNER,PROJECT)

def token(payload):
    def enc(value):return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return enc({'alg':'HS256'})+'.'+enc(payload)+'.eA'

class Auth:
    def __init__(self):
        self.calls=[]
        self.tokens={subject:token({'sub':subject,'iss':EXPECTED_ISSUER,'aud':'authenticated','role':'authenticated',
            'is_anonymous':False,'iat':time.time()-5,'exp':time.time()+300}) for subject in (OWNER,OTHER)}
    def transport(self):
        async def user(request):
            self.calls.append((request.method,str(request.url)))
            assert request.method=='GET' and str(request.url)=='http://127.0.0.1:57421/auth/v1/user'
            subject=next((s for s,t in self.tokens.items() if request.headers.get('authorization')=='Bearer '+t),None)
            if subject is None:return httpx.Response(401,json={})
            return httpx.Response(200,json={'id':subject,'aud':'authenticated','role':'authenticated',
                'is_anonymous':False,'email':'synthetic@example.invalid','email_confirmed_at':'2026-01-01T00:00:00Z'})
        return httpx.MockTransport(user)

class NoPool:
    def __init__(self):self.acquires=0
    async def getconn(self):
        self.acquires+=1
        raise AssertionError('REAL DB FORBIDDEN')
    def close(self):pass

class Repo:
    def __init__(self):self.calls=[];self.saved=None;self.error=None;self.mutate=None;self.flags=(True,True)
    async def call(self,kind,args):
        subject,project,*rest=args[:-1]
        self.calls.append((kind,subject,project,*rest))
        if subject!=OWNER or project!=PROJECT:raise Fault(404,'dataset_not_found')
        if self.error:raise self.error
        if kind=='enqueue':
            dataset,raw=rest
            if dataset!=DATASET:raise Fault(404,'dataset_not_found')
            value=dto(raw);created=self.saved is None
            if self.saved is not None and self.saved['request_sha256']!=value['request_sha256']:
                raise Fault(409,'preprocessing_request_conflict')
            self.saved=copy.deepcopy(value)
        else:
            if self.saved is None:raise Fault(404,'preprocessing_job_not_found')
            if (kind=='request' and rest!=[DATASET,RID]) or (kind=='job' and rest!=[JOB]):
                raise Fault(404,'preprocessing_job_not_found')
            value=copy.deepcopy(self.saved)
        if self.mutate:self.mutate(value)
        return p.QueueObservation(p.canonical(value),created,*self.flags) if kind=='enqueue' else value
    async def enqueue(self,*args):return await self.call('enqueue',args)
    async def read_request(self,*args):return await self.call('request',args)
    async def read_job(self,*args):return await self.call('job',args)

def scope(auth,method='GET',*,detail=False,subject=OWNER):
    path='/api/v1/preprocessing-jobs/'+JOB if detail else '/api/v1/datasets/'+DATASET+'/preprocessing-jobs'
    query='project_id='+PROJECT+('&request_id='+RID if method=='GET' and not detail else '')
    h=[(b'host',b'127.0.0.1:57430')]
    if subject is not None:h.append((b'authorization',('Bearer '+auth.tokens[subject]).encode()))
    if method=='POST':h.extend([(b'origin',b'http://127.0.0.1:4206'),(b'x-cyomics-request',b'1'),(b'content-type',b'application/json')])
    return {'type':'http','method':method,'path':path,'raw_path':path.encode(),'root_path':'',
        'query_string':query.encode(),'headers':h,'asgi':{'version':'3.0','spec_version':'2.3'},
        'http_version':'1.1','scheme':'http','server':('127.0.0.1',57430),'client':('127.0.0.1',1)}

async def invoke(app,s,raw=b'',receive=None):
    q=asyncio.Queue();q.put_nowait({'type':'http.request','body':raw,'more_body':False})
    out=[]
    async def send(message):out.append(message)
    await app(s,receive or q.get,send)
    return out

def status(out):return out[0]['status']
def value(out):return json.loads(out[-1]['body'])
def code(out):return value(out)['error']['code']

