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
