"""Run with PYTHONPATH=tools python -m bench.cli; credentials are environment-only."""
import argparse,json,os
from dataclasses import asdict
from pathlib import Path
import psycopg
from .models import FixtureSpec,RuntimeCell
from .fixture import capture_fixture
from .reset import mark_database,restore_fixture
from .seed import seed_database
from .runner import run_campaign
from .report import write_report
from .load_data import prepare_load_data
from .compatibility import validate_runtime_lock,probe_cell


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    migrate=sub.add_parser('migrate');mark=sub.add_parser('mark');mark.add_argument('--database',required=True)
    seed=sub.add_parser('seed');seed.add_argument('--small',action='store_true')
    capture=sub.add_parser('capture');capture.add_argument('--output',type=Path,required=True)
    for name in ['restore','recover']:
        s=sub.add_parser(name);s.add_argument('--fixture',type=Path,required=True);s.add_argument('--database',required=True)
    prepare=sub.add_parser('prepare-load');prepare.add_argument('--url',required=True);prepare.add_argument('--credentials',type=Path,required=True);prepare.add_argument('--output',type=Path,required=True);prepare.add_argument('--valid-seconds',type=int,default=600)
    credentials=sub.add_parser('credentials');credentials.add_argument('--count',type=int,default=200);credentials.add_argument('--output',type=Path,required=True)
    for name in ['run','pilot']:
        s=sub.add_parser(name);s.add_argument('--config',type=Path,required=True);s.add_argument('--output',type=Path,required=True)
    report=sub.add_parser('report');report.add_argument('--results',type=Path,required=True);report.add_argument('--output',type=Path,required=True)
    compatibility=sub.add_parser('compatibility');compatibility.add_argument('--cell')
    a=p.parse_args();dsn=os.environ.get('DATABASE_URL','')
    if a.command=='migrate':
        from sqlalchemy import create_engine
        from alembic.config import Config
        from alembic import command
        command.upgrade(Config('database/alembic.ini'),'head')
    elif a.command=='mark':print(mark_database(dsn,a.database))
    elif a.command=='seed':print(seed_database(dsn,FixtureSpec(users=100,posts=1000,follows_per_user=10,likes=2000,comments=500) if a.small else FixtureSpec()))
    elif a.command=='capture':print(json.dumps(asdict(capture_fixture(dsn,a.output)),indent=2))
    elif a.command in ('restore','recover'):restore_fixture(dsn,a.fixture,a.database);print('fixture restoration verified')
    elif a.command=='credentials':
        if not 1<=a.count<=50000:raise ValueError('invalid actor count')
        rows=[{'username':f'user_{i:05}','password':os.environ['SEED_PASSWORD']} for i in range(1,a.count+1)]
        a.output.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(a.output,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(rows,f)
    elif a.command=='prepare-load':print(prepare_load_data(a.url,a.credentials,a.output,a.valid_seconds))
    elif a.command in ('run','pilot'):
        if a.command=='pilot':
            cfg=json.loads(a.config.read_text());cfg.update(smoke=True,repeats=1,measure_seconds=30,warmup_seconds=30,rates=[25,50,100,200,400,800,1600,3200]);cfg['rates']=[r for r in cfg['rates'] if r<=cfg.get('max_rate',3200)]
            tmp=a.output/'pilot-config.json';tmp.parent.mkdir(parents=True,exist_ok=True);tmp.write_text(json.dumps(cfg));a.config=tmp
        results=run_campaign(a.config,a.output)
        print(json.dumps(results,indent=2))
        if any(r['status'] in ('failed','interrupted') or not r['reset_verified'] for r in results):raise SystemExit(1)
    elif a.command=='report':write_report(a.results,a.output)
    elif a.command=='compatibility':
        lock=validate_runtime_lock(json.loads(Path('docker/runtime-lock.json').read_text()))
        if a.cell:print(json.dumps(probe_cell(RuntimeCell(**next(c for c in lock['cells'] if c['id']==a.cell))),indent=2))
        else:print(json.dumps(lock,indent=2))

if __name__=='__main__':main()
