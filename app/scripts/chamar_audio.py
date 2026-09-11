"""Chamador WebRTC de teste: liga da Administração para bloco/apto, toca um tom de 440 Hz e mede o áudio recebido do celular.
Roda fora do app, num container com a rede do host (IP público direto, sem NAT do Docker):

  COOKIE=$(docker exec -i condominio-app python -c "import sys; sys.path.insert(0,'/app'); from sqlalchemy import select; import auth; \
    from db import SessionLocal; from models import AdminUser; db=SessionLocal(); a=db.scalar(select(AdminUser).where(AdminUser.master)); \
    print(auth.criar_sessao('admin', str(a.id)))")
  docker run --rm --network host -e COOKIE="$COOKIE" -v "$PWD/app/scripts:/w" python:3.13-slim \
    sh -c 'pip install -q aiortc websockets numpy && python /w/chamar_audio.py "$COOKIE" 17 004'

Validado em 2026-09-11 com o Bloco 17 · Apto 004 (Chrome Android, PWA instalado): toque, atender, áudio nos dois sentidos, desligar."""
import asyncio, json, sys, time, fractions, math
import numpy as np
from av import AudioFrame
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.mediastreams import AudioStreamTrack
from aiortc.sdp import candidate_from_sdp

COOKIE, BLOCO, APTO = sys.argv[1], sys.argv[2], sys.argv[3]
SR, DUR = 48000, 0.02


class Tom(AudioStreamTrack):
    """Tom de 440 Hz intermitente (1 s ligado, 1 s desligado)."""
    def __init__(self):
        super().__init__(); self.t = 0
    async def recv(self):
        n = int(SR * DUR); t = (np.arange(n) + self.t) / SR
        amostras = (0.3 * np.sin(2 * math.pi * 440 * t) * ((t % 2) < 1)).astype(np.float32)
        frame = AudioFrame.from_ndarray((amostras * 32767).astype(np.int16).reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = SR; frame.pts = self.t; frame.time_base = fractions.Fraction(1, SR); self.t += n
        await asyncio.sleep(DUR)
        return frame


async def main():
    pc = RTCPeerConnection(RTCConfiguration([RTCIceServer("stun:stun.l.google.com:19302")]))
    pc.addTrack(Tom())
    recebido = {"frames": 0, "energia": 0.0}

    @pc.on("track")
    def on_track(track):
        async def ler():
            while True:
                try:
                    f = await track.recv()
                except Exception:
                    return
                a = f.to_ndarray().astype(np.float32)
                recebido["frames"] += 1; recebido["energia"] += float(np.abs(a).mean())
        asyncio.ensure_future(ler())

    @pc.on("connectionstatechange")
    def on_state():
        print(time.strftime("%H:%M:%S"), "webrtc:", pc.connectionState, flush=True)

    async with websockets.connect("wss://jardimindependencia.com.br/ws/interfone", additional_headers={"Cookie": f"sessao={COOKIE}"}) as ws:
        await ws.send(json.dumps({"t": "chamar", "bloco": BLOCO, "apto": APTO}))
        cid, fim = None, time.time() + 90
        while time.time() < fim:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(1, fim - time.time())))
            except asyncio.TimeoutError:
                break
            t = m.get("t")
            if t in ("chamando", "tocando", "erro", "encerrada"):
                print(time.strftime("%H:%M:%S"), t, {k: v for k, v in m.items() if k in ("para", "status", "msg")}, flush=True)
            if t == "chamando":
                cid = m["chamada"]
            elif t == "atendida":
                print(time.strftime("%H:%M:%S"), "atendida: enviando oferta de áudio", flush=True)
                offer = await pc.createOffer(); await pc.setLocalDescription(offer)
                await ws.send(json.dumps({"t": "sdp", "chamada": cid, "dados": {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp}}))
                fim = time.time() + 40
            elif t == "sdp" and m.get("dados", {}).get("type") == "answer":
                await pc.setRemoteDescription(RTCSessionDescription(m["dados"]["sdp"], "answer"))
                print(time.strftime("%H:%M:%S"), "resposta SDP recebida; tom de 440 Hz tocando por ~30 s", flush=True)
            elif t == "ice" and m.get("dados") and m["dados"].get("candidate"):
                c = candidate_from_sdp(m["dados"]["candidate"].split(":", 1)[1])
                c.sdpMid, c.sdpMLineIndex = m["dados"].get("sdpMid"), m["dados"].get("sdpMLineIndex")
                await pc.addIceCandidate(c)
            elif t == "encerrada":
                break
        if cid:
            await ws.send(json.dumps({"t": "desligar", "chamada": cid}))
    await pc.close()
    print("áudio recebido do celular: %d quadros, energia média %.4f (%s)" % (recebido["frames"], recebido["energia"] / max(1, recebido["frames"]),
          "microfone chegando" if recebido["frames"] > 50 and recebido["energia"] / max(1, recebido["frames"]) > 1 else "sem som ou não conectou"))

asyncio.run(main())
