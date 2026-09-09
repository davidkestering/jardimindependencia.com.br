#!/bin/bash
# Teste de fumaça contra o site no ar: admin -> documento -> cadastro morador -> aprovação -> login morador -> download.
set -eu
cd "$(dirname "$0")/.."
. ./.env
H=https://jardimindependencia.com.br; R="--resolve jardimindependencia.com.br:443:127.0.0.1 -s"
JA=$(mktemp) JM=$(mktemp) TMPPDF=$(mktemp --suffix=.pdf)
CPF=52998224725; NASC=1990-03-05
falha(){ echo "FALHOU: $1"; exit 1; }
# admin login
c=$(curl $R -c $JA -o /dev/null -w '%{http_code}' -d "login=$ADMIN_LOGIN&senha=$ADMIN_SENHA_INICIAL" $H/admin/login); [ "$c" = 303 ] || falha "login admin ($c)"
c=$(curl $R -b $JA -o /dev/null -w '%{http_code}' $H/admin); [ "$c" = 200 ] || falha "painel admin ($c)"
c=$(curl $R -o /dev/null -w '%{http_code}' -d "login=$ADMIN_LOGIN&senha=errada" $H/admin/login); [ "$c" = 200 ] || falha "senha errada deveria voltar ao form ($c)"
# documento
printf '%%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>' > $TMPPDF
c=$(curl $R -b $JA -o /dev/null -w '%{http_code}' -F titulo="Teste smoke" -F categoria=Outros -F publico=1 -F arquivo=@$TMPPDF $H/admin/documentos); [ "$c" = 303 ] || falha "upload ($c)"
DOC=$(curl $R -b $JA $H/admin/documentos | grep -oE '/admin/documentos/[0-9a-f-]{36}' | head -1 | cut -d/ -f4); [ -n "$DOC" ] || falha "documento não listado"
# cadastro morador (pendente)
c=$(curl $R -o /dev/null -w '%{http_code}' -d "nome=Morador Teste&cpf=$CPF&nascimento=$NASC&bloco=17&apto=004" $H/morador/cadastro); [ "$c" = 200 ] || falha "cadastro ($c)"
curl $R -o /dev/null -d "cpf=$CPF&nascimento=$NASC" $H/morador/login | grep -q "aguarda aprovação" || true
out=$(curl $R -d "cpf=$CPF&nascimento=$NASC" $H/morador/login); echo "$out" | grep -q "aguarda aprova" || falha "pendente não deveria logar"
# aprovar
MID=$(curl $R -b $JA "$H/admin/moradores?status=pendente" | grep -oE '/admin/moradores/[0-9a-f-]{36}/status' | head -1 | cut -d/ -f4); [ -n "$MID" ] || falha "morador pendente não listado"
c=$(curl $R -b $JA -o /dev/null -w '%{http_code}' -d status=aprovado $H/admin/moradores/$MID/status); [ "$c" = 303 ] || falha "aprovar ($c)"
# login morador (CPF formatado e data dd/mm/aaaa)
c=$(curl $R -c $JM -o /dev/null -w '%{http_code}' -d "cpf=529.982.247-25&nascimento=05/03/1990" $H/morador/login); [ "$c" = 303 ] || falha "login morador ($c)"
curl $R -b $JM $H/morador | grep -q "Teste smoke" || falha "documento público não aparece"
c=$(curl $R -b $JM -o /dev/null -w '%{http_code} %{content_type}' $H/morador/documentos/$DOC); [[ "$c" == 200* ]] || falha "download ($c)"
c=$(curl $R -o /dev/null -w '%{http_code}' $H/morador/documentos/$DOC); [ "$c" = 303 ] || falha "download sem sessão deveria redirecionar ($c)"
# privado some
curl $R -b $JA -o /dev/null -d publico=0 $H/admin/documentos/$DOC/publico
c=$(curl $R -b $JM -o /dev/null -w '%{http_code}' $H/morador/documentos/$DOC); [ "$c" = 404 ] || falha "privado vazou ($c)"
# limpeza
curl $R -b $JA -o /dev/null -X POST $H/admin/documentos/$DOC/excluir; curl $R -b $JA -o /dev/null -X POST $H/admin/moradores/$MID/excluir
rm -f $JA $JM $TMPPDF; echo "SMOKE OK"
