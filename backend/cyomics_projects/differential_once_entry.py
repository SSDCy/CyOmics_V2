"""Root-only, no-listener entry using the ORIGINAL server factory's Owner pool.

No new bootstrap/gate/loader/credentials. Root must first authorize this fresh
process through its existing reviewed control path. Public/default call is off.
"""
from .settings import production_gate as _ORIGINAL_GATE,load_runtime_password as _ORIGINAL_LOADER


async def run_from_server(config=None,*,roots=None,activate_factory=None,cancelled=lambda:False):
    if config is None or roots is None or activate_factory is None:
        from .differential_worker_once import Outcome
        return Outcome('off','worker_unconfigured')
    from . import app as server,settings,repository
    from .differential_worker_once import OnceConfig,Outcome,need,run_once_owned
    application=None;transferred=False;outcome=None;closed=True;factory_called=False
    original_factory=server.create_app;original_pool=repository.FixedPool
    def original_bindings():
        need(settings.production_gate is _ORIGINAL_GATE and server.production_gate is _ORIGINAL_GATE
            and settings.load_runtime_password is _ORIGINAL_LOADER
            and repository.load_runtime_password is _ORIGINAL_LOADER
            and server.FixedPool is original_pool and repository.FixedPool is original_pool
            and server.create_app is original_factory)
    try:
        need(type(config) is OnceConfig and callable(config.verify) and callable(activate_factory))
        need(config.verify() is None) # Exact Root process/scope/source checks first.
        original_bindings()
        # Fresh dedicated process only. Root's separately reviewed activation
        # callback may arm the two formal operator gates AFTER its physical,
        # nonce/source/scope checks. This adapter never writes/reset those flags.
        need(settings.AUTH_POLICY_VERIFIED is False and settings.RUNTIME_START_APPROVED is False)
        roots.verify()
        def factory():
            nonlocal application,factory_called
            need(not factory_called);factory_called=True
            need(config.verify() is None);original_bindings()
            _ORIGINAL_GATE() # No gate/loader replacement or bypass.
            # No pool/Auth transport/loader DI: ORIGINAL production factory
            # creates its lazy FixedPool. All API feature flags stay default.
            application=original_factory()
            return application
        returned=activate_factory(factory)
        need(factory_called and application is not None and returned is application)
        need(config.verify() is None);original_bindings();_ORIGINAL_GATE()
        pool=application.state.repository.pool
        need(type(pool) is original_pool)
        transferred=True
        outcome=await run_once_owned(config,pool=pool,roots=roots,cancelled=cancelled)
    except BaseException:
        outcome=Outcome('reconciliation_pending','server_factory_or_entry_unknown',getattr(config,'job_id',None))
    finally:
        if application is not None:
            # Worker helper owns pool teardown after transfer, even on unknown.
            # Do not repeat a failed pool close or start an ASGI lifespan.
            if not transferred:
                try:
                    pool=application.state.repository.pool
                    handles=tuple(pool._idle)+tuple(pool._active)
                    pool.close();need(not pool._active and all(c.closed is True for c in handles))
                except BaseException:closed=False
            try:application.state.complete_imports.close()
            except BaseException:closed=False
            try:
                await application.state.authority.close()
                need(application.state.authority._client.is_closed is True)
            except BaseException:closed=False
    if not closed:
        return Outcome('reconciliation_pending','server_resources_teardown_unknown',
            None if outcome is None else outcome.job_id,None if outcome is None else outcome.attempt_id,
            None if outcome is None else outcome.result_id)
    return outcome
