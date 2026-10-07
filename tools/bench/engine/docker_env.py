"""Container environment channel isolated from the Docker client's environment."""

import re


def validate_container_environment(environment):
    for key, value in environment.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("container environment requires portable variable names")
        if not isinstance(value, str) or any(char in value for char in "\r\n\0"):
            raise ValueError("container environment values cannot contain CR, LF or NUL")


# Executed on the selected Docker host. Only JSON on stdin contains target values.
# Docker reads the inherited anonymous FD as an env-file; no secret file survives
# process exit, and neither the helper nor Docker inherits target configuration.
CREATE_WITH_ENV = r"""import json,os,re,shutil,subprocess,sys
payload=json.load(sys.stdin)
values=payload['environment']
for key,value in values.items():
 if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):
  raise ValueError('container environment requires portable variable names')
 if not isinstance(value,str) or any(char in value for char in '\r\n\0'):
  raise ValueError('container environment values cannot contain CR, LF or NUL')
docker=shutil.which('docker')
if docker is None: raise RuntimeError('Docker executable unavailable')
fd=os.memfd_create('bench-container-environment',flags=os.MFD_CLOEXEC)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(os.dup(fd),'w',encoding='utf-8',newline='') as output:
  output.write(''.join(key+'='+value+'\n' for key,value in values.items()))
 os.lseek(fd,0,os.SEEK_SET)
 result=subprocess.run([docker,'create','--env-file','/proc/self/fd/'+str(fd),*sys.argv[1:]],
                       pass_fds=(fd,),timeout=payload['timeout'])
 sys.exit(result.returncode)
finally:
 os.close(fd)
"""
