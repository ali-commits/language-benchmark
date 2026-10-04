"""Lightweight host counters, container resource state, and PostgreSQL activity."""
import json
import time
import threading
from pathlib import Path
import psycopg
from .transport import execute

HOST_PROBE="import json,pathlib,time; print(json.dumps({'time':time.time(),'stat':pathlib.Path('/proc/stat').read_text(),'meminfo':pathlib.Path('/proc/meminfo').read_text(),'netdev':pathlib.Path('/proc/net/dev').read_text(),'loadavg':pathlib.Path('/proc/loadavg').read_text()}))"

class Collector:
    def __init__(self,hosts: dict,dsn: str,path: Path):
        self.hosts=hosts;self.dsn=dsn;self.path=path;self.stop=threading.Event();self.errors=[]
    def sample(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('w') as f:
            while not self.stop.is_set():
                entry={'time':time.time(),'hosts':{}}
                for name,host in self.hosts.items():
                    try:
                        entry['hosts'][name]=json.loads(execute(host,['python3','-c',HOST_PROBE],timeout=10).stdout)
                        if name=='backend':entry['docker']=execute(host,['docker','stats','--no-stream','--format','{{json .}}'],timeout=10).stdout
                    except Exception as e:self.errors.append(type(e).__name__);entry['hosts'][name]={'error':type(e).__name__}
                try:
                    with psycopg.connect(self.dsn,connect_timeout=5) as c:
                        entry['database']=c.execute('SELECT numbackends,xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,temp_bytes FROM pg_stat_database WHERE datname=current_database()').fetchone()
                        entry['waits']=c.execute('SELECT wait_event_type,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY wait_event_type').fetchall()
                except Exception as e:self.errors.append(type(e).__name__);entry['database_error']=type(e).__name__
                f.write(json.dumps(entry)+'\n');f.flush();self.stop.wait(2)
    def __enter__(self):
        self.thread=threading.Thread(target=self.sample,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set();self.thread.join(30)
        if self.thread.is_alive():self.errors.append('collector shutdown timeout')

def rds_metrics(identifier: str,region: str,start,end) -> dict:
    import boto3
    cw=boto3.client('cloudwatch',region_name=region);rds=boto3.client('rds',region_name=region)
    result={}
    for name in ['CPUUtilization','DatabaseConnections','FreeableMemory','ReadIOPS','WriteIOPS','ReadLatency','WriteLatency','ReadThroughput','WriteThroughput','DiskQueueDepth']:
        result[name]=cw.get_metric_statistics(Namespace='AWS/RDS',MetricName=name,Dimensions=[{'Name':'DBInstanceIdentifier','Value':identifier}],StartTime=start,EndTime=end,Period=60,Statistics=['Average','Maximum'])['Datapoints']
    result['events']=rds.describe_events(SourceIdentifier=identifier,SourceType='db-instance',StartTime=start,EndTime=end)['Events']
    result['instance']=rds.describe_db_instances(DBInstanceIdentifier=identifier)['DBInstances'][0]
    # Whitelist metadata; AWS response can contain internal service endpoints and secret references.
    result['instance']={k:result['instance'].get(k) for k in ['DBInstanceClass','EngineVersion','AvailabilityZone','StorageType','AllocatedStorage','Iops','StorageThroughput','DBParameterGroups']}
    return result
