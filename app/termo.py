"""Declaração de veracidade exibida no cadastro. O texto aceito fica gravado em morador.termo_texto;
se este texto mudar, quem aceitou a versão antiga é levado a aceitar de novo ao entrar (routers/morador.py)."""
import hashlib

TERMO = ("Declaro, sob as penas da lei, que as informações prestadas são verdadeiras e que sou a pessoa indicada. "
         "A prestação de informações falsas ou o uso da identidade de outra pessoa configura crime de falsidade ideológica (art. 299) "
         "e falsa identidade (art. 307) do Código Penal (Decreto-Lei nº 2.848/1940) e sujeitará o responsável às medidas judiciais cabíveis. "
         "Os dados serão usados apenas para gestão do acesso, nos termos da LGPD (Lei nº 13.709/2018).")


def versao(texto: str | None) -> str:
    return hashlib.sha256((texto or "").encode()).hexdigest()[:8]


VERSAO_ATUAL = versao(TERMO)
