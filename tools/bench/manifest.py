import hashlib
import json
import platform
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

SECRET_KEYS=('password','secret','token','credentials','database_url','dsn')

def redact(value):
    if isinstance(value,dict):return {k:('[redacted]' if any(s in k.lower() for s in SECRET_KEYS) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str) and (value.startswith('postgresql://') or value.startswith('postgresql+psycopg://')):return '[redacted connection]'
    return value

def collect_manifest(campaign: dict,trial: dict) -> dict:
    rev=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],text=True).strip())
    return redact({'campaign':campaign,'trial':trial,'git_revision':rev,'dirty':dirty,'orchestrator':platform.platform(),'label':'LOCAL SMOKE' if campaign.get('smoke') else campaign['mode'].upper(),'lock_checksum':hashlib.sha256(Path('docker/runtime-lock.json').read_bytes()).hexdigest()})

def atomic_json(path: Path,data: dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_suffix('.tmp');p.write_text(json.dumps(redact(data),indent=2,default=str)+'\n');p.replace(path)
