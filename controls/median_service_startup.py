"""Root-reviewed future fixed Median once shim, SOURCE ONLY default off.
Root must supply exact source/backend/startup approval pins in a separately reviewed
private control copy. No CLI/ENV/user JSON may fill CONFIG or self-approve it.
"""
CONFIG=None

def make_api_config():
    if CONFIG is None:return None
    from cyomics_projects.median_on_demand import MedianDispatchConfig,prepare
    return prepare(CONFIG)

def main():
    import sys
    if CONFIG is None or sys.argv[1:]!=['--once']:
        print('Median service off; fixed Root-reviewed CONFIG required.',file=sys.stderr);return 2
    from cyomics_projects.preprocess_median_service_bootstrap import main_from_server
    raw=sys.stdin.buffer.read(1025)
    return main_from_server(CONFIG.service,raw)

if __name__=='__main__':raise SystemExit(main())
