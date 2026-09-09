import asyncio, json, sys
try:
    from websockets.asyncio.client import connect
    def conn(url, cookie): return connect(url, additional_headers={'Cookie': f'sessao={cookie}'} if cookie else None)
except ImportError:
    from websockets import connect
    def conn(url, cookie): return connect(url, extra_headers={'Cookie': f'sessao={cookie}'} if cookie else None)
from sqlalchemy import select
from db import SessionLocal
from models import Morador
import auth
with SessionLocal() as db:
    a = db.scalar(select(Morador).where(Morador.cpf=='52998224725')); b = db.scalar(select(Morador).where(Morador.cpf=='11144477735'))
    ca, cb = auth.criar_sessao('morador', str(a.id)), auth.criar_sessao('morador', str(b.id))
URL='ws://localhost:8000/ws/interfone'
async def rx(ws, t, timeout=5):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if m['t'] == t: return m
async def main():
    async with conn(URL, ca) as A, conn(URL, cb) as B:
        await A.send(json.dumps({'t':'chamar','bloco':'16','apto':'102'}))
        ch = await rx(A, 'chamando'); assert ch['para'] == 'Bloco 16 · Apto 102', ch
        toc = await rx(B, 'tocando'); assert toc['de'] == 'Bloco 17 · Apto 004' and toc['chamada'] == ch['chamada'], toc
        await B.send(json.dumps({'t':'atender','chamada':ch['chamada']})); await rx(A, 'atendida')
        await A.send(json.dumps({'t':'sdp','chamada':ch['chamada'],'dados':{'type':'offer','sdp':'x'}})); s = await rx(B, 'sdp'); assert s['dados']['type']=='offer'
        await B.send(json.dumps({'t':'ice','chamada':ch['chamada'],'dados':{'candidate':'c'}})); await rx(A, 'ice')
        await B.send(json.dumps({'t':'desligar','chamada':ch['chamada']})); e = await rx(A, 'encerrada'); assert e['status']=='encerrada'
        await A.send(json.dumps({'t':'chamar','bloco':'99','apto':'999'})); assert (await rx(A,'erro'))['msg']
        await B.send(json.dumps({'t':'chamar','bloco':'PORTARIA'})); ch2 = await rx(B,'chamando'); assert ch2['para']=='PORTARIA'
        await B.send(json.dumps({'t':'desligar','chamada':ch2['chamada']}))
    try:
        async with conn(URL, None) as C: await C.recv()
        print('ERRO: sem sessao deveria fechar'); sys.exit(1)
    except Exception as e: print('sem sessao fecha ->', type(e).__name__)
    print('RESULT WS INTERFONE OK')
asyncio.run(main())
