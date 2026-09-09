// Interfone virtual: sinalização por WebSocket, áudio por WebRTC, toque por WebAudio.
// Carregado em todas as páginas de sessão (morador/admin); a página /morador/interfone adiciona a discagem.
(() => {
  const STUN = [{ urls: 'stun:stun.l.google.com:19302' }];
  let ws, pc, stream, chamada = null, papel = null, toque = null, reconectar = 2000;
  const $ = s => document.querySelector(s);

  // ---- UI de chamada (overlay criado uma vez) ----
  const ov = document.createElement('div'); ov.className = 'chamada'; ov.id = 'ifone-overlay';
  ov.innerHTML = `<div><div class="eyebrow" style="color:#e8c3ad" id="ov-eyebrow">Interfone</div><h2 id="ov-titulo"></h2><p id="ov-sub"></p>
    <div class="acoes"><button class="atender pulsa" id="ov-atender">Atender</button><button class="recusar" id="ov-recusar">Recusar</button><button class="recusar" id="ov-desligar" hidden>Desligar</button></div>
    <audio id="ov-audio" autoplay playsinline></audio></div>`;
  document.body.appendChild(ov);
  if (!document.querySelector('style[data-ifone]')) { const st = document.createElement('style'); st.dataset.ifone = 1; st.textContent = `.chamada{display:none;position:fixed;inset:0;background:rgba(30,22,16,.92);z-index:30;place-items:center;color:#efe4d8;text-align:center;padding:20px}.chamada.ativa{display:grid}.chamada h2{color:#fff;font-size:30px;margin:8px 0}.chamada .acoes{display:flex;gap:14px;justify-content:center;margin-top:20px}.chamada button{font:700 16px "Nunito Sans",sans-serif;border:0;border-radius:999px;padding:14px 26px;cursor:pointer}.chamada .atender{background:#2e8b57;color:#fff}.chamada .recusar{background:#b23a2a;color:#fff}.pulsa{animation:pulsa 1.2s infinite}@keyframes pulsa{50%{transform:scale(1.06)}}`; document.head.appendChild(st); }
  const mostrar = (titulo, sub, botoes) => { $('#ov-titulo').textContent = titulo; $('#ov-sub').textContent = sub; $('#ov-atender').hidden = !botoes.atender; $('#ov-recusar').hidden = !botoes.recusar; $('#ov-desligar').hidden = !botoes.desligar; ov.classList.add('ativa'); };
  const esconder = () => ov.classList.remove('ativa');

  // ---- toque (WebAudio, sem arquivo) ----
  function tocar(tipo) {
    pararToque();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const g = ctx.createGain(); g.gain.value = 0.15; g.connect(ctx.destination);
    let vivo = true;
    const ciclo = () => { if (!vivo) return;
      const o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = tipo === 'entrada' ? 880 : 440; o.connect(g); o.start(); o.stop(ctx.currentTime + (tipo === 'entrada' ? 0.4 : 1.0));
      if (tipo === 'entrada') { const o2 = ctx.createOscillator(); o2.frequency.value = 660; o2.connect(g); o2.start(ctx.currentTime + 0.5); o2.stop(ctx.currentTime + 0.9); }
      setTimeout(ciclo, tipo === 'entrada' ? 2000 : 3000); };
    ciclo(); toque = { parar: () => { vivo = false; ctx.close(); } };
    if (navigator.vibrate && tipo === 'entrada') navigator.vibrate([500, 300, 500]);
  }
  function pararToque() { if (toque) { toque.parar(); toque = null; } }

  // ---- WebRTC ----
  async function prepararPC() {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    pc = new RTCPeerConnection({ iceServers: STUN });
    stream.getTracks().forEach(t => pc.addTrack(t, stream));
    pc.ontrack = e => { $('#ov-audio').srcObject = e.streams[0]; };
    pc.onicecandidate = e => { if (e.candidate) enviar({ t: 'ice', chamada, dados: e.candidate }); };
    pc.onconnectionstatechange = () => { if (pc.connectionState === 'connected') $('#ov-sub').textContent = 'Em conversa'; };
  }
  function limpar() { pararToque(); if (pc) { pc.close(); pc = null; } if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; } chamada = null; papel = null; esconder(); }

  // ---- sinalização ----
  const enviar = m => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };
  function conectar() {
    ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/interfone');
    ws.onopen = () => { reconectar = 2000; status('Interfone conectado', 'on'); };
    ws.onclose = () => { status('Interfone desconectado, reconectando…', 'off'); setTimeout(conectar, reconectar); reconectar = Math.min(reconectar * 2, 30000); };
    ws.onmessage = async ev => {
      const m = JSON.parse(ev.data);
      if (m.t === 'tocando') { if (chamada) return; chamada = m.chamada; papel = 'destino'; mostrar('Chamada de ' + m.de, 'Interfone virtual', { atender: 1, recusar: 1 }); tocar('entrada'); }
      else if (m.t === 'chamando') { chamada = m.chamada; papel = 'origem'; mostrar('Chamando ' + m.para, 'Aguardando atender…', { desligar: 1 }); tocar('saida'); }
      else if (m.t === 'atendida' && m.chamada === chamada) { pararToque(); $('#ov-sub').textContent = 'Conectando áudio…'; await prepararPC(); const of = await pc.createOffer(); await pc.setLocalDescription(of); enviar({ t: 'sdp', chamada, dados: pc.localDescription }); }
      else if (m.t === 'sdp' && m.chamada === chamada) { if (!pc) await prepararPC(); await pc.setRemoteDescription(m.dados); if (m.dados.type === 'offer') { const an = await pc.createAnswer(); await pc.setLocalDescription(an); enviar({ t: 'sdp', chamada, dados: pc.localDescription }); } }
      else if (m.t === 'ice' && m.chamada === chamada && pc) { try { await pc.addIceCandidate(m.dados); } catch (e) {} }
      else if (m.t === 'encerrada' && m.chamada === chamada) { const txt = { recusada: 'Chamada recusada', perdida: 'Ninguém atendeu', encerrada: 'Chamada encerrada' }[m.status] || 'Chamada encerrada'; mostrar(txt, '', {}); pararToque(); setTimeout(limpar, 1500); }
      else if (m.t === 'erro') { status(m.msg, 'off'); setTimeout(() => status('Interfone conectado', 'on'), 4000); }
    };
  }
  function status(txt, cls) { const el = $('#ifone-status'); if (el) { el.textContent = txt; el.className = 'status ' + cls; } }

  $('#ov-atender').onclick = async () => { pararToque(); $('#ov-atender').hidden = true; $('#ov-recusar').hidden = true; $('#ov-desligar').hidden = false; $('#ov-sub').textContent = 'Conectando áudio…'; try { await prepararPC(); } catch (e) { $('#ov-sub').textContent = 'Microfone não liberado'; } enviar({ t: 'atender', chamada }); };
  $('#ov-recusar').onclick = () => { enviar({ t: 'recusar', chamada }); limpar(); };
  $('#ov-desligar').onclick = () => { enviar({ t: 'desligar', chamada }); limpar(); };

  // ---- discagem (só na página do interfone) ----
  document.querySelectorAll('[data-chamar]').forEach(b => b.onclick = () => enviar({ t: 'chamar', bloco: b.dataset.chamar }));
  const form = $('#form-chamar'); if (form) form.onsubmit = e => { e.preventDefault(); enviar({ t: 'chamar', bloco: form.bloco.value, apto: form.apto.value }); };

  // ---- PWA + push ----
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js', { scope: '/' }).catch(() => {});
  const btnPush = $('#btn-push');
  if (btnPush) btnPush.onclick = async () => {
    const st = $('#push-status');
    try {
      const reg = await navigator.serviceWorker.ready;
      const perm = await Notification.requestPermission(); if (perm !== 'granted') { st.textContent = 'Permissão de notificação negada.'; return; }
      const key = Uint8Array.from(atob(btnPush.dataset.vapid.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      const r = await fetch('/interfone/push', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(sub) });
      st.textContent = r.ok ? 'Notificações ativadas neste aparelho.' : 'Não foi possível salvar a inscrição.';
    } catch (e) { st.textContent = 'Este navegador não suporta notificações push (no iPhone, instale o site na tela inicial primeiro).'; }
  };
  let promptInstalar; window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); promptInstalar = e; const b = $('#btn-instalar'); if (b) { b.hidden = false; b.onclick = () => promptInstalar.prompt(); } });

  conectar();
})();
