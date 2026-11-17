"""Default-off single-RPC worker input reader; no launch, cache or authority bypass.

The trusted pool's actual function-only DB role is an external configuration gate.
Factory returns the server's frozen BoundRaw dataclass; its dicts are detached,
not recursively immutable and not an authorization capability.
"""
import asyncio
from dataclasses import fields, is_dataclass
import hashlib
from uuid import UUID
from psycopg.pq import TransactionStatus as T
from .repository import abort, status
from .settings import Fault
from .validation import canonical_uuid
from . import raw_qc_contract as contract

SQL = ('SELECT source_json,execution_json,content '
       'FROM cyomics_app.raw_qc_bound_source(%s,%s)')
MAX_TEXT = 4096
MAX_CONTENT = 26214400


def invalid():
    return Fault(503, 'raw_qc_persisted_invalid')


def decode_row(row, check):
    try:
        if type(row) not in (tuple, list) or len(row) != 3:
            raise ValueError()
        source_text, execution_text, content = row
        values = []
        for text, validator in ((source_text, contract.source_value),
                                (execution_text, contract.execution_value)):
            check()
            if type(text) is not str or not 1 <= len(text) <= MAX_TEXT:
                raise ValueError()
            raw = text.encode('utf-8')
            if not 1 <= len(raw) <= MAX_TEXT:
                raise ValueError()
            value = contract.strict_document(raw, MAX_TEXT)
            validator(value)
            # Canonical reparse detaches every dictionary and normalizes counts.
            values.append(contract.strict_document(contract.canonical(value).encode('utf-8'), MAX_TEXT))
        source, execution = values
        check()
        if type(content) is memoryview:
            if (content.ndim != 1 or content.itemsize != 1 or not content.contiguous
                    or not 1 <= content.nbytes <= MAX_CONTENT):
                raise ValueError()
            content = content.tobytes()
        elif type(content) is not bytes:
            raise ValueError()
        if (not 1 <= len(content) <= MAX_CONTENT
                or len(content) != source['source_bytes']):
            raise ValueError()
        check()
        if hashlib.sha256(content).hexdigest() != source['source_sha256']:
            raise ValueError()
        check()
        return source, execution, content
    except (TimeoutError, asyncio.CancelledError):
        raise
    except Exception:
        raise invalid() from None


class WorkerBoundSource:
    def __init__(self, pool=None, bound_raw_factory=None):
        self.pool = pool
        self.factory = bound_raw_factory

    async def load_bound_source(self, job_id, attempt_id, budget):
        if self.pool is None or self.factory is None or not callable(self.factory):
            raise Fault(503, 'raw_qc_unavailable')
        if not canonical_uuid(job_id) or not canonical_uuid(attempt_id):
            raise Fault(400, 'invalid_raw_qc_request')
        connection = None

        def discard(task):
            if not task.cancelled() and task.exception() is None:
                late = task.result()
                try:
                    abort(late)
                    if late.closed:
                        self.pool.release(late)
                except Exception:
                    # No raw callback exception/credential logging. An unclosed
                    # socket is withheld, never returned for reuse.
                    pass

        def safe_abort():
            # Budget.abandon must always reach cancellation and consumption even
            # when synchronous driver teardown raises. Finally below separately
            # requires closure before any release; this callback never logs.
            try:
                abort(connection)
            except BaseException:
                pass

        async def run(operation):
            budget.check()
            return await budget.run(operation, 2, safe_abort)

        async def execute(sql, params=()):
            return await run(lambda: connection.execute(sql, params))

        try:
            # Lazy acquisition, including the post-scheduling deadline check.
            budget.check()
            connection = await budget.run(self.pool.getconn, 1, discard=discard)
            if connection.closed or status(connection) != T.IDLE:
                raise Fault(503, 'raw_qc_unavailable')
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY')
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), "
                          "pg_catalog.set_config('statement_timeout', %s, true), "
                          "pg_catalog.set_config('lock_timeout', %s, true)",
                          ('', str(max(1, min(2000, int(budget.remaining()*1000)))), '1000'))
            cursor = await execute(SQL, (UUID(job_id), UUID(attempt_id)))
            first = await run(cursor.fetchone)
            second = await run(cursor.fetchone)
            if second is not None:
                raise invalid()
            if first is None:
                raise Fault(409, 'raw_qc_attempt_conflict')
            source, execution, content = decode_row(first, budget.check)
            source_snapshot, execution_snapshot = contract.canonical(source), contract.canonical(execution)
            value = self.factory(job_id=job_id, attempt_id=attempt_id,
                                 source=source, execution=execution, content=content)
            if (not is_dataclass(value) or not value.__dataclass_params__.frozen
                    or {f.name for f in fields(value)} != {'job_id','attempt_id','source','execution','content'}
                    or value.job_id != job_id or value.attempt_id != attempt_id
                    or contract.canonical(value.source) != source_snapshot
                    or contract.canonical(value.execution) != execution_snapshot
                    or type(value.content) is not bytes or value.content != content):
                raise invalid()
            budget.check()
            await execute('COMMIT')
            budget.check()
            if status(connection) != T.IDLE:
                raise invalid()
            return value
        except asyncio.CancelledError:
            raise
        except Fault as error:
            allowed = {409: {'raw_qc_attempt_conflict'},
                       503: {'raw_qc_unavailable', 'raw_qc_persisted_invalid'},
                       504: {'raw_qc_timeout'}}
            if error.code in allowed.get(error.status, set()):
                raise Fault(error.status, error.code) from None
            raise invalid() from None
        except TimeoutError:
            raise Fault(504, 'raw_qc_timeout') from None
        except Exception:
            raise invalid() from None
        finally:
            if connection is not None:
                # Even successful reads close the socket before release. This
                # removes any prior session GUC/role state rather than assuming
                # transaction-local empty identity erased a poisoned session.
                try:
                    abort(connection)
                    if not connection.closed:
                        raise invalid()
                    self.pool.release(connection)
                except Exception:
                    raise invalid() from None
