#!/bin/bash
# Fases 3 e 4: cobrança em atraso -> inadimplência -> voto registrado mas não computado; unidade em dia -> voto válido.
set -eu
cd "$(dirname "$0")/.."
. ./.env
H=https://jardimindependencia.com.br; R="--resolve jardimindependencia.com.br:443:127.0.0.1 -s"
JA=$(mktemp) J1=$(mktemp) J2=$(mktemp)
falha(){ echo "FALHOU: $1"; exit 1; }
curl $R -c $JA -o /dev/null -d "login=$ADMIN_LOGIN&senha=$ADMIN_SENHA_INICIAL" $H/admin/login
# dois moradores aprovados: bloco 17/004 (inadimplente) e 16/102 (em dia)
curl $R -b $JA -o /dev/null -d "nome=Inadimplente Teste&cpf=52998224725&nascimento=1990-03-05&bloco=17&apto=004" $H/admin/moradores
curl $R -b $JA -o /dev/null -d "nome=Em Dia Teste&cpf=11144477735&nascimento=1985-10-20&bloco=16&apto=102" $H/admin/moradores
# cobrança vencida para 17/004 e cobrança futura para 16/102
c=$(curl $R -b $JA -o /dev/null -w '%{http_code}' -d "descricao=Taxa teste atrasada&valor=250,00&vencimento=2026-08-01&bloco=17&apto=004" $H/admin/financeiro); [ "$c" = 303 ] || falha "cobranca ($c)"
curl $R -b $JA -o /dev/null -d "descricao=Taxa teste futura&valor=250,00&vencimento=2099-01-01&bloco=16&apto=102" $H/admin/financeiro
curl $R -b $JA "$H/admin/financeiro?atraso=1" | grep -q "Bloco 17 · Apto 004" || falha "inadimplência não listada"
# assembleia aberta com uma pauta
c=$(curl $R -b $JA -o /dev/null -w '%{http_code} %{redirect_url}' -d "titulo=Assembleia teste&abre_em=2026-01-01T00:00&fecha_em=2099-01-01T00:00" $H/admin/assembleias); AID=$(echo "$c" | grep -oE '[0-9a-f-]{36}$'); [ -n "$AID" ] || falha "assembleia ($c)"
curl $R -b $JA -o /dev/null --data-urlencode "texto=Aprovar reforma?" --data-urlencode $'opcoes=Sim\nNão' $H/admin/assembleias/$AID/pautas
PID=$(curl $R -b $JA $H/admin/assembleias/$AID | grep -oE 'pautas/[0-9a-f-]{36}/excluir' | head -1 | cut -d/ -f2); [ -n "$PID" ] || falha "pauta"
# logins dos moradores
curl $R -c $J1 -o /dev/null -d "cpf=52998224725&nascimento=1990-03-05" $H/morador/login
curl $R -c $J2 -o /dev/null -d "cpf=11144477735&nascimento=1985-10-20" $H/morador/login
curl $R -b $J1 $H/morador/financeiro | grep -q "em atraso" || falha "morador não vê atraso"
curl $R -b $J1 $H/morador/assembleias/$AID | grep -q "registro de inadimplência" || falha "aviso inadimplente ausente"
OID=$(curl $R -b $J2 $H/morador/assembleias/$AID | grep -oE 'value="[0-9a-f-]{36}" required>Sim' | head -1 | grep -oE '[0-9a-f-]{36}'); [ -n "$OID" ] || falha "opção"
# votam os dois em "Sim"
r1=$(curl $R -b $J1 -o /dev/null -w '%{redirect_url}' -d "pauta_$PID=$OID" $H/morador/assembleias/$AID/votar); echo "$r1" | grep -q "inadimpl" || falha "msg inadimplente ($r1)"
r2=$(curl $R -b $J2 -o /dev/null -w '%{redirect_url}' -d "pauta_$PID=$OID" $H/morador/assembleias/$AID/votar); echo "$r2" | grep -q "Voto%20registrado" || falha "msg voto válido ($r2)"
# segundo voto da mesma unidade não conta
r3=$(curl $R -b $J2 -o /dev/null -w '%{redirect_url}' -d "pauta_$PID=$OID" $H/morador/assembleias/$AID/votar); echo "$r3" | grep -q "Nenhum" || falha "voto duplicado aceito ($r3)"
# resultado admin: 1 válido, 1 não computado
pg=$(curl $R -b $JA $H/admin/assembleias/$AID); echo "$pg" | grep -q "Votos válidos: 1 " || falha "válidos != 1"; echo "$pg" | grep -q "não computados): 1" || falha "não computados != 1"
# limpeza
curl $R -b $JA -o /dev/null -X POST $H/admin/assembleias/$AID/excluir
for cid in $(curl $R -b $JA "$H/admin/financeiro?status=" | grep -oE '/admin/financeiro/[0-9a-f-]{36}/status' | cut -d/ -f4 | sort -u); do curl $R -b $JA -o /dev/null -d status=cancelada $H/admin/financeiro/$cid/status; done
docker exec condominio-db psql -U condominio -d condominio -qc "delete from cobranca where descricao like 'Taxa teste%'; delete from morador where cpf in ('52998224725','11144477735');"
rm -f $JA $J1 $J2; echo "SMOKE FASES 3-4 OK"
