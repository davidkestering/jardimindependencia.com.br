# Condomínio Jardim Independência — site e sistemas internos

Site institucional e sistemas internos do **Condomínio Residencial Jardim Independência** (Ananindeua, PA), em produção em [jardimindependencia.com.br](https://jardimindependencia.com.br).

Desenvolvido por **David Alexandre de Souza Kestering** ([davidkestering.com](https://davidkestering.com)). O uso pelo condomínio é regido pela [Declaração de Doação](https://jardimindependencia.com.br/doacao), documento pessoal mantido apenas no servidor; o código é publicado sob a licença descrita em [LICENSE](LICENSE), com a autoria preservada em [NOTICE](NOTICE).

## O que o sistema faz

**Site público**: páginas do condomínio, galeria, localização, contato (com captcha e declaração de responsabilidade), comunicados públicos e a declaração de doação.

**Área do condômino** (acesso por CPF e data de nascimento; 1 titular por apartamento):
- solicitação de cadastro com declaração de veracidade, captcha e aprovação pela administração;
- escolha do apartamento administrado na sessão (mesmo CPF com vários aptos);
- residentes do apartamento (moradores e inquilinos) e transferência de acesso;
- garagem e veículos: garagem de cada apartamento conforme a convenção, garagens que o apartamento utiliza e veículos com a garagem de cada um;
- animais de estimação: nome, tipo, raça e foto (opcionais) de cada animal do apartamento, com edição e exclusão, para identificar um animal visto solto; ou a informação de que a unidade não possui animais;
- comunicados com aviso por e-mail e notificação push;
- documentos do condomínio; assembleias e enquetes com voto por unidade (unidade inadimplente vota sem contar);
- registro de ocorrências, imutável, com anexos (PDF, imagens e 1 vídeo de até 30 s por mensagem) e respostas da administração;
- Fale Conosco: sugestões, reclamações, ideias, conselhos e elogios à administração, com imagens e 1 vídeo de até 30 s opcionais; não é ocorrência (sem número, resposta ou finalização) e a administração apenas lê;
- interfone virtual: PWA instalável, chamadas de voz WebRTC entre apartamentos, portaria e administração.

**Administração** (usuários com áreas liberadas; dois usuários mestres): moradores e aprovações, documentos com categorias e envio múltiplo, comunicados com pré-visualização, Fale Conosco (leitura das mensagens, com quem enviou), inadimplência, garagem e veículos (consulta e correção do vínculo apartamento-garagem), animais de estimação (quantas e quais unidades registraram, informaram não possuir ou ainda não informaram, com filtros e fotos), assembleias, enquetes, ocorrências, usuários e histórico de auditoria, com filtro exclusivo das tentativas de login que falharam nas duas áreas (total e contagem por dia do período, para detectar tentativas de invasão). Toda ação registra quem, data/hora e IP; nada é apagado fisicamente (exclusão lógica).

## Stack

- Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Jinja2, uvicorn
- PostgreSQL 18 (UUIDs em todas as tabelas)
- ClamAV para varredura de uploads; Web Push (VAPID); WebSocket + WebRTC no interfone
- Docker Compose; proxy nginx externo com TLS (Let's Encrypt); e-mail via Mailu (SMTP)

## Como rodar

```bash
cp .env.example .env        # preencha senhas, SECRET_KEY e SMTP
docker compose up -d --build
```

O app aplica as migrações (`alembic upgrade head`) ao subir, cria as 396 unidades (blocos 01–27) mais Portaria e Administração, e cria o primeiro usuário da administração apenas se não existir nenhum (`ADMIN_LOGIN` / `ADMIN_SENHA_INICIAL`). As fotos da galeria (`app/static/img/condominio/`), a página da Declaração de Doação com a assinatura do autor e os documentos enviados **não** estão no repositório. Uploads ficam em `data/uploads`, assinaturas do ClamAV em `data/clamav`, chaves VAPID em `data/vapid`. O proxy deve repassar `Upgrade`/`Connection: "upgrade"` para o WebSocket do interfone e aceitar corpo de até 100 MB.

## Checagens

Scripts ponta a ponta em `app/scripts/check_*.py` (rodam contra o banco real sem enviar e-mail e limpam o que criam):

```bash
docker exec condominio-app python scripts/check_cadastro.py
```

Os testes nunca usam apartamento real: trabalham nas unidades de teste do Bloco 99 (Apto 999 a principal; 998, 997 e 996 quando o teste precisa de mais de uma), criadas inativas pelo próprio app. Para o site elas não existem: ficam fora de toda lista, seleção, contagem e busca por bloco e apto, e só o processo do teste as enxerga (`scripts/unidades_teste.py`). `scripts/check_unidade_teste.py` confere essa invisibilidade.

Há também `scripts/chamar_audio.py`, um chamador WebRTC de teste do interfone (instruções no arquivo).

## Segurança

Sessões assinadas (httponly, secure, SameSite); toda rota privada exige sessão do tipo certo; bloqueio por tentativas de login e captcha matemático; uploads com validação de assinatura interna, bloqueio de PDF com conteúdo ativo e antivírus; cabeçalhos de segurança e limite de requisições no proxy. Nenhuma senha está neste repositório: tudo vem do `.env`.
