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

TERMO_OCORRENCIA = ("Declaro que os fatos relatados nesta ocorrência são verdadeiros e de meu conhecimento, e que assumo a responsabilidade "
                    "pelo seu conteúdo. Estou ciente de que relatar fatos inverídicos, fazer denúncia falsa ou ofender a honra de terceiros "
                    "pode configurar os crimes de denunciação caluniosa (art. 339), calúnia (art. 138), difamação (art. 139) e injúria (art. 140) "
                    "do Código Penal (Decreto-Lei nº 2.848/1940), além de responsabilidade civil por danos, e sujeitará o responsável às medidas "
                    "judiciais cabíveis. Os dados e anexos serão usados apenas para apuração e resposta pela administração, nos termos da LGPD "
                    "(Lei nº 13.709/2018).")
