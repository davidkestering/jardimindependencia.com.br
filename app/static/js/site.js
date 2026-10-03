// menu mobile + carrossel do hero (sem dependências)
document.querySelector('.hamb')?.addEventListener('click', () => document.querySelector('.menu').classList.toggle('aberto'));
const slides = [...document.querySelectorAll('.hero .slide')];
if (slides.length > 1) {
  const pontos = document.querySelector('.hero .pontos'), legenda = document.querySelector('.hero .legenda');
  let i = 0, timer;
  slides.forEach((_, k) => { const b = document.createElement('button'); b.setAttribute('aria-label', 'Foto ' + (k + 1)); b.onclick = () => { ir(k); reiniciar(); }; pontos.appendChild(b); });
  function ir(k) { slides[i].classList.remove('ativo'); pontos.children[i].classList.remove('ativo'); i = k; slides[i].classList.add('ativo'); pontos.children[i].classList.add('ativo'); legenda.textContent = slides[i].dataset.legenda; }
  function reiniciar() { clearInterval(timer); if (!matchMedia('(prefers-reduced-motion: reduce)').matches) timer = setInterval(() => ir((i + 1) % slides.length), 5000); }
  ir(0); reiniciar();
}

// Menu interno: marca a página atual e, no celular (faixa que rola de lado), deixa o item dela à vista.
(function () {
  const nav = document.querySelector('.submenu');
  const atual = nav && [...nav.querySelectorAll('a')].filter(a => (location.pathname + '/').startsWith(a.pathname + '/')).sort((a, b) => b.pathname.length - a.pathname.length)[0];
  if (!atual) return;
  atual.setAttribute('aria-current', 'page');
  nav.scrollLeft += atual.getBoundingClientRect().left - nav.getBoundingClientRect().left - (nav.clientWidth - atual.offsetWidth) / 2;
})();

// Tabelas no celular: o CSS mostra cada linha como ficha; aqui o título de cada coluna vai para data-label da célula.
document.querySelectorAll('table').forEach(t => {
  const titulos = [...t.querySelectorAll('th')].map(th => th.textContent.trim());
  if (titulos.length >= 7) t.classList.add('larga');
  t.querySelectorAll('tr').forEach(tr => {
    if (tr.querySelector('th')) return tr.classList.add('cab');
    [...tr.children].forEach((td, i) => {
      if (td.colSpan > 1) return;
      td.dataset.label = titulos[i] || '';
      if (td.querySelector('button, .btn')) td.classList.add('acoes');
      else if (i === 0 && !td.children.length) td.classList.add('tit');  // 1ª coluna só com texto: título da ficha
    });
  });
});

// CPF: máscara enquanto digita e validação dos dígitos verificadores em todo campo name="cpf" (o servidor valida de novo).
(function () {
  function cpfValido(d) {
    if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false;
    for (const n of [9, 10]) { let s = 0; for (let i = 0; i < n; i++) s += d[i] * (n + 1 - i); if ((s * 10 % 11) % 10 !== +d[n]) return false; }
    return true;
  }
  document.querySelectorAll('input[name="cpf"]').forEach(inp => {
    inp.setAttribute('maxlength', '14'); inp.setAttribute('autocomplete', 'off');
    const aviso = document.createElement('small'); aviso.style.cssText = 'display:block;color:#7a2e12;font-weight:600;margin-top:4px'; aviso.hidden = true;
    inp.insertAdjacentElement('afterend', aviso);
    const checar = () => {
      const d = inp.value.replace(/\D/g, '').slice(0, 11);
      inp.value = d.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2');
      const ok = d.length < 11 ? null : cpfValido(d);
      inp.setCustomValidity(ok === false ? 'CPF inválido' : (d.length && d.length < 11 ? 'CPF incompleto' : ''));
      aviso.textContent = ok === false ? 'CPF inválido: confira os números.' : ''; aviso.hidden = ok !== false;
    };
    inp.addEventListener('input', checar); inp.addEventListener('blur', checar); if (inp.value) checar();
  });
})();

// Confirmação antes de enviar: <form data-confirm="Pergunta?">. Usa <dialog> nativo em vez de window.confirm(),
// que dentro do app iOS (WKWebView) devolve "cancelar" sem mostrar nada e o formulário nunca é enviado.
(function () {
  let dlg;
  function abrir(form) {
    if (!dlg) {
      dlg = document.createElement('dialog'); dlg.className = 'confirma';
      dlg.innerHTML = '<p></p><div><button type="button" class="btn sec">Cancelar</button><button type="button" class="btn">Confirmar</button></div>';
      document.body.appendChild(dlg);
    }
    dlg.querySelector('p').textContent = form.dataset.confirm;
    const [cancelar, confirmar] = dlg.querySelectorAll('button');
    cancelar.onclick = () => dlg.close();
    confirmar.onclick = () => { dlg.close(); form.submit(); };
    dlg.showModal();
  }
  document.addEventListener('submit', e => {
    const form = e.target.closest('form[data-confirm]');
    if (form) { e.preventDefault(); abrir(form); }
  });
})();

// Anexos com vídeo: <input type="file" data-video-s="30"> avisa, antes do envio, se há mais de um vídeo ou se ele passa do
// limite de segundos. É só conforto para não esperar o envio à toa: o servidor confere de novo.
(function () {
  document.addEventListener('change', e => {
    const inp = e.target.closest('input[type=file][data-video-s]');
    if (!inp) return;
    inp.setCustomValidity('');
    const videos = [...inp.files].filter(f => /\.(mp4|mov)$/i.test(f.name)), max = +inp.dataset.videoS;
    if (videos.length > 1) { inp.setCustomValidity('Envie no máximo 1 vídeo.'); inp.reportValidity(); return; }
    if (!videos.length) return;
    const v = document.createElement('video'), url = URL.createObjectURL(videos[0]);
    v.preload = 'metadata';
    v.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      if (![...inp.files].includes(videos[0])) return;  // a seleção mudou enquanto o vídeo carregava
      if (v.duration > max + 1) { inp.setCustomValidity(`O vídeo tem ${Math.round(v.duration)} segundos; o máximo é ${max}.`); inp.reportValidity(); }
    };
    v.onerror = () => URL.revokeObjectURL(url);  // o navegador não lê o formato: o servidor decide
    v.src = url;
  });
})();
