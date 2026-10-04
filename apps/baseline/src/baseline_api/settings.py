from pydantic import Field,model_validator
from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=None,extra='ignore')
    database_url: str
    jwt_secret: str=Field(min_length=32)
    jwt_ttl_seconds: int=Field(default=3600,ge=600,le=86400)
    workers: int=Field(default=2,ge=1,le=2)
    mode: str='local'
    run_dir: str=''
    run_id: str=''
    expected_gil: bool=True
    expected_jit: bool=False
    thread_limit: int=16

    @model_validator(mode='after')
    def validate_db(self):
        from urllib.parse import urlsplit,parse_qs
        if self.thread_limit !=16: raise ValueError('fixed thread limit is 16')
        u=urlsplit(self.database_url.replace('postgresql+psycopg://','postgresql://'))
        if u.scheme!='postgresql': raise ValueError('PostgreSQL required')
        q=parse_qs(u.query)
        if self.mode=='aws' and (q.get('sslmode')!=['verify-full'] or not q.get('sslrootcert')):
            raise ValueError('AWS DB requires sslmode=verify-full and sslrootcert')
        return self
