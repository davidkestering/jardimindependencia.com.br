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
