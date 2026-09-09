# Site + Gestão do Condomínio Jardim Independência

## Contexto

Novo projeto em `/root/PROJETO_CONDOMINIO_JARDIM_INDEPENDENCIA` (pasta vazia). Site institucional do condomínio
(Av. Gov. Hélio Gueiros, 48, Ananindeua/PA, CEP 67120-370, em frente ao La Salle) com vitrine de fotos, paleta
em tons pastéis/bege, e área logada: administração (login+senha) e condômino (CPF + data de nascimento).
Funções: documentos publicados pela administração, cobranças (PIX/boleto), boletos em atraso, votação de assembleias,
e depois Interfone Virtual (PWA com chamada por unidade, portaria e administração). Domínio `jardimindependencia.com.br`
já comprado (DNS na registro.br, zona vazia hoje). E-mail `contato@jardimindependencia.com.br` depois.

Entendi as tarefas. Sim.

## Infra existente que será reaproveitada (nada novo de servidor)

- VPS `srv1367261`: IPv4 `187.77.40.230`, IPv6 `2a02:4780:6e:4f5::1`.
- Proxy central: `proxy-nginx` + `proxy-certbot`, um arquivo por host em `/root/proxy/conf.d/`, rede `proxy-net`
  (padrão: bloco 80 com ACME + redirect, bloco 443 com `resolver 127.0.0.11` e `set $upstream_x`). Agente
  `nginx-domain-manager` já automatiza isso.
- Mailu em `/root/mail` (Postfix/Dovecot/Rspamd/Roundcube), já serve 3 domínios; adicionar domínio novo é config,
  não um servidor novo.
- Convenção dos projetos recentes: um container por app, sem porta publicada, join em `proxy-net`.
- **Regra do projeto (definida por você):** tudo em Docker, banco Postgres na versão mais recente também em Docker,
  e todos os containers com volumes locais (bind mounts dentro da pasta do projeto).

## DNS — o que configurar AGORA na registro.br (Editar zona)

| Tipo | Nome | Valor | Obs |
|---|---|---|---|
| A | `@` | `187.77.40.230` | site |
| AAAA | `@` | `2a02:4780:6e:4f5::1` | igual ao bestbidev.com |
| A | `www` | `187.77.40.230` | registro.br não aceita CNAME no apex; `www` como A |
| AAAA | `www` | `2a02:4780:6e:4f5::1` | |
| A | `mail` | `187.77.40.230` | host do e-mail (segue padrão mail.<dominio>) |
| A | `webmail` | `187.77.40.230` | Roundcube |
| A | `mail-admin` | `187.77.40.230` | painel Mailu |
| MX | `@` | `10 mail.jardimindependencia.com.br.` | |
| TXT | `@` | `v=spf1 mx ip4:187.77.40.230 ~all` | mesmo SPF dos domínios irmãos |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@jardimindependencia.com.br; adkim=s; aspf=s` | |
| TXT | `dkim._domainkey` | **entrego depois** | chave só existe após criar o domínio no Mailu (fase 6) |

Propagação registro.br: minutos a poucas horas. Só depois do `dig +short jardimindependencia.com.br` responder
`187.77.40.230` é possível emitir o certificado SSL.

## Stack (decisão)

- **FastAPI + Jinja2 + HTMX/JS leve, container `condominio-app`**, igual ao padrão `tempodemaresia`. Uma só
  linguagem/serviço cobre site, admin, área do condômino, WebSocket (sinalização do interfone) e web push.
- **PostgreSQL 18 (imagem `postgres:18`, a mais recente publicada; tag verificada)** em container próprio
  `condominio-db`, dados em bind mount `./data/postgres:/var/lib/postgresql`, sem porta publicada, rede privada
  `condominio-net`. Acesso via SQLAlchemy 2 + psycopg 3 + Alembic (migrations). Healthcheck `pg_isready`; o app só
  sobe com `depends_on: condition: service_healthy` (mesmo padrão do sistema_eleitoral).
- Credenciais do banco em `.env` (não versionado), `.env.example` versionado.
- CSS próprio com custom properties (paleta bege/pastel extraída das fotos), sem build de frontend.
- Uploads (PDF de documentos, fotos) em bind mount `./data/uploads:/data/uploads`, servidos pelo app com checagem
  de permissão.
- Backup: script `backup.sh` com `pg_dump` para `./data/backups/` + cron diário no host (retenção 30 dias).
- Sessão por cookie assinado (`itsdangerous`, já vem com Starlette). Senha admin com `bcrypt`.
  Rate limit de login em memória (por IP e por CPF) — CPF+nascimento é credencial fraca; isso é obrigatório.
- E-mail transacional via SMTP do Mailu (`mailu-front:587`, smtplib stdlib).

Estrutura:
```
PROJETO_CONDOMINIO_JARDIM_INDEPENDENCIA/
  docker-compose.yml          # condominio-app (proxy-net + condominio-net), condominio-db (condominio-net)
  .env  .env.example          # POSTGRES_USER/PASSWORD/DB, SECRET_KEY, SMTP, VAPID
  backup.sh                   # pg_dump -> ./data/backups
  app/Dockerfile  requirements.txt
  app/main.py                 # app, routers, static, templates
  app/db.py  models.py  auth.py  mail.py
  app/routers/{site,admin,morador,financeiro,votacao,interfone}.py
  app/templates/...  app/static/{css,js,img,icons}
  app/migrations/             # alembic
  data/postgres/              # bind mount do Postgres (fora da imagem, dentro do projeto)
  data/uploads/  data/backups/
  docs/superpowers/specs/2026-09-09-condominio-design.md
```

## Modelo de dados (núcleo)

**Regra (definida por você): nenhuma chave incremental. Toda tabela usa `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`**
(nativo do Postgres, sem extensão). Chaves estrangeiras também UUID. URLs usam o UUID (ex.: `/admin/documentos/<uuid>`).

- `unidade(id, bloco, apto, ativa)` — numeração `AAN`: andar (0 = térreo) + posição 1–4. Seed:
  - blocos 01–12: térreo `001–004` + 1º andar `101–104` = 8 unidades por bloco (96);
  - blocos 13–27: `001–004`, `101–104`, `201–204`, `301–304`, `401–404` = 20 por bloco (300).
  - Constraint `UNIQUE(bloco, apto)`; `bloco` guardado com 2 dígitos (`01`…`27`), `apto` com 3 dígitos.
  **Atenção:** a regra dá 396 unidades, você citou 376. Seed pela regra, admin desativa o que não existir.
  Unidades especiais: `PORTARIA`, `ADMINISTRACAO` (alvos do interfone).
- `morador(id, unidade_id, nome, cpf, nascimento, email, telefone, status: pendente|aprovado|bloqueado)`.
- `admin_user(id, login, senha_hash, nome)`.
- `documento(id, titulo, categoria, arquivo, publico, criado_em)`.
- `cobranca(id, unidade_id, descricao, valor, vencimento, status, pix_copia_cola, boleto_url, gateway_ref)`.
- `assembleia(id, titulo, abre_em, fecha_em)`, `pauta(id, assembleia_id, texto)`, `opcao`, `voto(unidade_id, pauta_id, opcao_id, inadimplente_no_voto bool, votado_em)` UNIQUE(unidade_id,pauta_id).
- `push_subscription(morador_id, endpoint, keys)`, `chamada(de_unidade, para_unidade, status, ts)`.

## Fases (cada uma entregue e testada antes da próxima)

### Fase 0 — Domínio e esqueleto
1. Você configura o DNS acima. Eu valido com `dig`.
2. Scaffold FastAPI + compose (app + Postgres 18, bind mounts em `./data`) + Dockerfile, app em `proxy-net`,
   migration inicial via Alembic, backup.sh funcionando.
3. Config Nginx `jardimindependencia.com.br` (+`www`) via agente `nginx-domain-manager`, certificado Let's Encrypt.
4. Página "em breve" no ar em HTTPS.

### Fase 1 — Site institucional
1. Localizar fotos do condomínio (fachada, piscina, salão, quadra, parques, churrasqueiras, pista de skate, portaria)
   em anúncios imobiliários, Google Maps e redes. Baixar para `app/static/img/`, registrar a fonte de cada uma.
   **Ressalva:** fotos de terceiros podem ter direitos; o ideal é a administração fornecer fotos próprias e trocarmos.
2. Extrair paleta (Pillow, k-means simples) → variáveis CSS bege claro / bege escuro / acentos.
3. Páginas: Home (carrossel CSS/JS leve), O Condomínio (áreas comuns listadas por você), Localização (mapa embed),
   Contato (form → e-mail contato@), links "Área do Condômino" / "Administração". Responsivo, acessível.

### Fase 2 — Login, cadastro, documentos
1. Login admin (login+senha) e seed do primeiro admin por variável de ambiente/CLI.
2. Login condômino CPF + nascimento (valida dígitos do CPF, rate limit, bloqueio temporário).
3. Admin: CRUD unidades (seed), CRUD moradores, aprovação de auto-cadastro (fila "pendentes").
4. Auto-cadastro público: bloco, apto, nome, CPF, nascimento, telefone, e-mail → status `pendente`, e-mail para admin.
5. Documentos: upload PDF/imagem, categoria, flag `publico`. Condômino vê só os públicos.

### Fase 3 — Financeiro
1. Admin lança cobranças por unidade (ou em lote para todas as unidades ativas: taxa do mês).
2. Condômino vê cobranças abertas, em atraso (vencimento < hoje e não paga) e histórico.
3. Interface `Gateway` com implementação `Manual` (admin marca como pago). Quando você escolher o provedor
   (Asaas/Efí/MercadoPago), entra a implementação com geração de PIX/boleto e webhook de baixa. Não escreverei
   código do provedor antes da escolha.

### Fase 4 — Assembleias e votação
1. Admin cria assembleia com pautas e opções, janela de abertura/fechamento.
2. Um voto por unidade por pauta (constraint no banco), só morador aprovado, só dentro da janela.
3. **Inadimplência (regra sua):** toda unidade pode votar. Se a unidade tiver cobrança em atraso no momento do voto,
   o voto é registrado com `inadimplente_no_voto = true` e o condômino vê a mensagem
   "Voto apresentado, porém a unidade está com registro de inadimplência." Esses votos ficam guardados, aparecem
   na tela do admin em linha separada ("votos não computados por inadimplência"), mas **não entram na contagem final**.
4. Resultado por pauta (contagem dos votos válidos), ata exportável em PDF simples depois se quiser.

### Fase 5 — Interfone Virtual (PWA)
1. `manifest.json` + service worker: instalável no Android/iOS, push via VAPID (`pywebpush`).
2. Sinalização: WebSocket FastAPI (`/ws/interfone`), mapa `unidade → conexões` (mesmo padrão do comunicador-api).
3. Chamada de áudio WebRTC P2P entre navegadores; STUN público; `ponytail:` TURN (coturn) só se NAT bloquear.
4. Ligar para: bloco+apto, Portaria, Administração. Toque de telefone no site logado; push com som quando em
   segundo plano; tela de chamada recebida (atender/recusar). Portaria e administração têm "ramal" próprio.
5. APK (Capacitor, mesmo esquema do comunicador) só se o PWA não bastar.

### Fase 6 — E-mail do domínio
1. Adicionar `mail.jardimindependencia.com.br` em `HOSTNAMES` do `/root/mail/mailu.env`, host no
   `mail-hostnames-acme.conf`, reemitir cert `mailservers`, configs `webmail.` e `mail-admin.` no proxy (padrão existente).
2. Criar domínio no painel Mailu, gerar DKIM → te entrego o TXT `dkim._domainkey`.
3. Criar caixa `contato@` (e `postmaster@`). Testar envio/recebimento e mail-tester.

## Verificação por fase

- Fase 0: `curl -I https://jardimindependencia.com.br` → 200; `certbot certificates` lista o domínio.
- Fase 1: Lighthouse (chrome-devtools) acessibilidade/performance; responsivo em mobile via Playwright screenshot.
- Fase 2: testes pytest (auth, CPF inválido, rate limit, morador pendente não loga, documento privado não vaza).
- Fase 3/4: testes de regras (atraso, 1 voto por unidade, janela fechada rejeita).
- Fase 5: duas sessões Playwright, chamada A→B toca e conecta; push recebido com aba fechada (teste manual no celular).
- Fase 6: envio para Gmail chega na caixa de entrada, DKIM/SPF/DMARC pass.

## Próximo passo imediato após aprovação

Escrever a spec em `docs/superpowers/specs/2026-09-09-condominio-design.md`, criar repositório git, executar Fase 0
(enquanto o DNS propaga, o scaffold já fica pronto).
