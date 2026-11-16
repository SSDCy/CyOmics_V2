"""Parameterized owner queries and explicit bounded transactions; no import I/O."""
import asyncio
from collections import deque
import os
from uuid import UUID
from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from .settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, Fault, load_runtime_password, unavailable
from .validation import PUBLIC_COLUMNS, encode_cursor, public_project


def status(connection):
    return connection.pgconn.transaction_status


def abort(connection):
    # Synchronous socket teardown; never return an active/uncertain transaction.
    if not connection.closed:
        connection.pgconn.finish()


class FixedPool:
    """At most three sockets. One connect attempt, no retry worker or warm-up."""
    def __init__(self):
        self._slots = asyncio.Semaphore(3)
        self._idle = deque()
        self._active = set()
        self._closing = False

    async def getconn(self):
        await self._slots.acquire()
        password = None
        try:
            if self._closing:
                raise unavailable()
            while self._idle:
                connection = self._idle.popleft()
                if not connection.closed and status(connection) == TransactionStatus.IDLE:
                    self._active.add(connection)
                    return connection
                abort(connection)
            # Refuse environmental libpq policy rather than letting it select a
            # service file or another endpoint. Never inspect/log PG* values.
            if any(key.startswith('PG') for key in os.environ):
                raise unavailable()
            password = load_runtime_password()
            connection = await AsyncConnection.connect(host=DB_HOST, hostaddr=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=password, connect_timeout=2, require_auth='scram-sha-256', sslmode='disable', gssencmode='disable', autocommit=True)
            if self._closing:
                abort(connection)
                raise unavailable()
            self._active.add(connection)
            return connection
        except BaseException:
            self._slots.release()
            raise
        finally:
            password = None

    def release(self, connection):
        try:
            self._active.discard(connection)
            if not self._closing and not connection.closed and status(connection) == TransactionStatus.IDLE:
                self._idle.append(connection)
            else:
                abort(connection)
        finally:
            self._slots.release()

    def close(self):
        self._closing = True
        while self._idle:
            abort(self._idle.popleft())
        for connection in self._active:
            abort(connection)


class Repository:
    def __init__(self, pool):
        self.pool = pool

    async def _transaction(self, subject, budget, operation, creating=False):
        connection = None
        outcome = budget.create
        async def execute(sql, params=(), committing=False):
            budget.check()
            async def operation():
                if committing:
                    outcome.commit_started = True
                return await connection.execute(sql, params)
            return await budget.run(operation, 2, lambda: abort(connection))
        async def query(sql, params=(), many=False):
            milliseconds = max(1, min(2000, int(budget.remaining()*1000)))
            await execute("SELECT pg_catalog.set_config('statement_timeout', %s, true)", (str(milliseconds),))
            cursor = await execute(sql, params)
            return await budget.run(lambda: cursor.fetchall() if many else cursor.fetchone(), 2, lambda: abort(connection))
        def discard_late_acquisition(task):
            if not task.cancelled() and task.exception() is None:
                late = task.result()
                abort(late)
                self.pool.release(late)
        try:
            budget.check()
            connection = await budget.run(self.pool.getconn, 1, discard=discard_late_acquisition)
            if status(connection) != TransactionStatus.IDLE:
                raise unavailable()
            await execute('BEGIN ISOLATION LEVEL READ COMMITTED' + ('' if creating else ' READ ONLY'))
            await execute("SELECT pg_catalog.set_config('app.user_id', %s, true), pg_catalog.set_config('statement_timeout', %s, true), pg_catalog.set_config('lock_timeout', %s, true)", (subject, str(max(1,min(2000,int(budget.remaining()*1000)))), str(max(1,min(1000,int(budget.remaining()*1000))))))
            result = await operation(query)
            budget.check()
            await execute('COMMIT', committing=creating)
            if creating:
                outcome.committed = True
            budget.check()
            if status(connection) != TransactionStatus.IDLE:
                raise unavailable()
            return result
        except BaseException as exc:
            # After a COMMIT was sent, an idle/lost connection cannot prove that
            # it rolled back. Never turn a lost response into an automatic retry.
            rollback_possible = connection is not None and not connection.closed and status(connection) in (TransactionStatus.INTRANS, TransactionStatus.INERROR)
            if rollback_possible and budget.remaining():
                try:
                    await execute('ROLLBACK')
                    if status(connection) == TransactionStatus.IDLE:
                        outcome.rollback_confirmed = True
                except BaseException:
                    abort(connection)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, Fault) and not creating:
                raise
            raise outcome.failure() if creating else unavailable() from None
        finally:
            if connection is not None:
                self.pool.release(connection)

    async def list(self, subject, limit, position, budget):
        async def operation(query):
            sql = 'SELECT ' + PUBLIC_COLUMNS + ' FROM cyomics_app.projects WHERE owner_id=%s'
            params = [UUID(subject)]
            if position is not None:
                sql += ' AND (created_at, project_id) < (%s, %s)'
                params.extend(position)
            sql += ' ORDER BY created_at DESC, project_id DESC LIMIT %s'
            params.append(limit+1)
            rows = await query(sql, tuple(params), many=True)
            visible = rows[:limit]
            cursor = encode_cursor(visible[-1][5], visible[-1][0]) if len(rows) > limit else None
            return {'items':[public_project(row) for row in visible], 'nextCursor':cursor}
        return await self._transaction(subject, budget, operation)

    async def detail(self, subject, project_id, budget):
        async def operation(query):
            row = await query('SELECT ' + PUBLIC_COLUMNS + ' FROM cyomics_app.projects WHERE owner_id=%s AND project_id=%s', (UUID(subject),UUID(project_id)))
            if row is None:
                raise Fault(404,'project_not_found')
            return {'project':public_project(row)}
        return await self._transaction(subject, budget, operation)

    async def create(self, subject, values, budget):
        async def operation(query):
            owner = UUID(subject)
            workspace = await query('SELECT workspace_id FROM cyomics_app.workspaces WHERE owner_id=%s', (owner,))
            if workspace is None:
                budget.create.write_started = True
                workspace = await query('INSERT INTO cyomics_app.workspaces (owner_id) VALUES (%s) ON CONFLICT (owner_id) DO NOTHING RETURNING workspace_id', (owner,))
                if workspace is None:
                    # A fresh READ COMMITTED statement sees a concurrent winner.
                    workspace = await query('SELECT workspace_id FROM cyomics_app.workspaces WHERE owner_id=%s', (owner,))
            if workspace is None:
                raise unavailable()
            budget.create.write_started = True
            row = await query('INSERT INTO cyomics_app.projects (owner_id, created_by, workspace_id, name, description, species) VALUES (%s,%s,%s,%s,%s,%s) RETURNING ' + PUBLIC_COLUMNS, (owner,owner,workspace[0],values['name'],values['description'],values['species']))
            return {'project':public_project(row)}
        return await self._transaction(subject, budget, operation, creating=True)
