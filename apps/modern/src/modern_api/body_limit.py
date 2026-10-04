from starlette.responses import JSONResponse

class BodyLimit:
    """Bound content-length and chunked request bodies before JSON parsing."""
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            msg=await receive()
            if msg['type']=='http.disconnect': return
            size+=len(msg.get('body',b''))
            if size>16384:
                return await JSONResponse({'detail':'request body too large'},status_code=413)(scope,receive,send)
            chunks.append(msg)
            if not msg.get('more_body',False): break
        async def bounded_receive():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope,bounded_receive,send)
