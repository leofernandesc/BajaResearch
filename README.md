# BAJA Research

BAJA Research é um plugin standalone para o Hermes Agent que encontra
literatura acadêmica aplicável a equipes Baja SAE. A versão 0.2 foi desenhada
para uso local no terminal e no WhatsApp self-chat, com prioridade para TCCs,
monografias, dissertações e teses extensas.

O plugin não modifica o core do Hermes e não inclui aplicação web, FastAPI,
PostgreSQL, Redis, Docker obrigatório, RAG, banco vetorial, processamento em
massa de PDFs ou suporte multiusuário.

## Política de qualidade

Um trabalho só é recomendado quando satisfaz simultaneamente estes critérios:

- corresponde ao foco técnico solicitado;
- possui contexto Baja SAE, Mini Baja, Formula SAE/Formula Student, ATV ou
  veículo off-road;
- não é centrado em veículo elétrico, híbrido ou célula a combustível, salvo
  quando esse assunto for pedido explicitamente;
- possui texto completo gratuito em PDF;
- o plugin conseguiu abrir esse PDF anonimamente e confirmou MIME de PDF ou o
  prefixo real `%PDF-`.

DOI, selo open access, página de editora, landing page de repositório e HTTP
200 não são prova suficiente de acesso gratuito. O plugin retorna menos
resultados quando necessário, em vez de completar a lista com material pago,
fora do tema ou com link não verificado.

O Google Scholar não é consultado nem raspado. O MVP usa APIs e repositórios
com interfaces próprias, evitando uma integração frágil e sem API pública
adequada para este fluxo.

## Arquitetura

```text
Hermes chat / gateway / WhatsApp self-chat
                    |
          skill + 5 ferramentas
                    |
              SearchRouter
       +------------+-------------+
       |            |             |
 Oasisbr/BDTD    OpenAlex    Semantic Scholar
  TCCs/teses      global        fallback
       +------------+-------------+
                    |
        Crossref: metadados por DOI
        DSpace/Unpaywall: candidatos de PDF
                    |
      normalização -> deduplicação -> gates
                    |
       verificação anônima de PDF -> ranking
                    |
          SQLite persistente do Hermes
```

Na versão local verificada, Hermes Agent v0.20.6, a API pública usada é
`register(ctx)`, `ctx.register_tool(...)`, `ctx.register_skill(...)` e
`ctx.state.data_dir`. Essa versão não expõe `plugin_db()` para o plugin; por
isso, o SQLite fica no diretório persistente fornecido pelo Hermes, nunca no
diretório instalável.

As cinco ferramentas são:

- `search_academic_papers`: busca, normaliza, deduplica, filtra, valida PDFs e
  ranqueia;
- `get_paper`: consolida detalhes de DOI, ID OpenAlex, ID Semantic Scholar ou
  ID interno;
- `find_related_papers`: usa relações nativas e busca de fallback, mantendo os
  mesmos requisitos de relevância e PDF gratuito;
- `format_citation`: produz ABNT ou BibTeX somente com campos disponíveis;
- `research_cache_stats`: mostra cache, schema, fontes e último estado das
  APIs.

## Fontes e papéis

1. **Oasisbr**: primeira fonte para TCCs, monografias, dissertações e teses
   brasileiras.
2. **BDTD**: complemento de trabalhos longos quando o Oasisbr não fornece
   cobertura suficiente.
3. **OpenAlex**: descoberta acadêmica global, incluindo artigos e metadados de
   acesso aberto.
4. **Semantic Scholar**: fallback global, citações, enriquecimento e trabalhos
   relacionados. Uma chave opcional reduz limitações de uso.
5. **Crossref**: resolução e enriquecimento bibliográfico por DOI. Não é usado
   como fonte primária de candidatos, pois licença ou link da editora não
   garantem PDF gratuito.
6. **Unpaywall**: resolvedor opcional de cópias abertas por DOI. Toda URL ainda
   passa pela verificação real de PDF.

O resolvedor de repositórios conhece DSpace 6 e DSpace 7 e usa OAI-PMH/METS/ORE
para instalações DSpace 8/9 que protegem a API REST. Ele tenta converter a
landing page em um bitstream original. Uma falha em uma fonte não derruba a
busca inteira; o erro, inclusive HTTP 429, aparece em `sources` e `warnings`.

## Normalização, deduplicação e ranking

O modelo interno preserva título, autores, ano, resumo, instituição/venue,
tipo de documento, DOI, IDs acadêmicos, citações, tópicos, fontes, proveniência
e evidência de acesso. Payloads brutos extensos não são enviados ao Hermes.

A deduplicação usa, nesta ordem:

1. DOI canônico;
2. IDs acadêmicos conhecidos;
3. título normalizado e ano;
4. correspondência fuzzy conservadora, somente quando os sinais são seguros.

Antes do ranking, gates rígidos eliminam foco técnico incorreto e ausência de
contexto Baja/Formula/off-road. Dentro de cada classe documental, o score usa
52% de relevância técnica, 25% de contexto de aplicação, 8% de relevância da
fonte, 5% de completude, 4% de confirmação multi-fonte, 4% de citações
log-normalizadas e 2% de recência. Assim, citações e novidade não dominam a
lista. A ordenação padrão coloca trabalhos longos antes de artigos; um título
contendo apenas a palavra “TCC” não é classificado como TCC.

## Pré-requisitos

- Hermes Agent instalado e disponível como `hermes`;
- Python 3.11 ou superior;
- `httpx>=0.27,<1` no Python usado pelo Hermes;
- acesso à internet para buscas reais.

Não existe credencial obrigatória. Chaves opcionais melhoram limites ou
resolução de acesso.

## Instalação local

O checkout usado no desenvolvimento está em
`/home/leofernandesc/BajaResearch`. Para carregá-lo diretamente:

```bash
mkdir -p ~/.hermes/plugins
ln -s /home/leofernandesc/BajaResearch ~/.hermes/plugins/baja-research
hermes plugins enable baja-research --no-allow-tool-override
```

Se o link já existir e apontar para esse checkout, não o recrie. Para instalar
do GitHub em vez do checkout local:

```bash
hermes plugins install git@github.com:leofernandesc/BajaResearch.git --no-enable
hermes plugins enable baja-research --no-allow-tool-override
```

O plugin não recebe permissão para sobrescrever ferramentas nativas. Valide a
instalação com:

```bash
hermes plugins doctor /home/leofernandesc/BajaResearch --ci
hermes plugins list --plain --no-bundled
hermes plugins show baja-research
```

Se o ambiente do Hermes não tiver `httpx`:

```bash
~/.hermes/hermes-agent/venv/bin/pip install 'httpx>=0.27,<1'
```

## Configuração

O Hermes informa o arquivo de ambiente ativo com:

```bash
hermes config env-path
```

Use `.env.example` como referência. Não versione o `.env` real.

```dotenv
OPENALEX_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=
CROSSREF_MAILTO=
UNPAYWALL_EMAIL=

BAJA_RESEARCH_CACHE_TTL_HOURS=24
BAJA_RESEARCH_SOURCE_CACHE_TTL_HOURS=24
BAJA_RESEARCH_REQUEST_TIMEOUT_SECONDS=8
BAJA_RESEARCH_GLOBAL_TIMEOUT_SECONDS=25
BAJA_RESEARCH_MAX_RETRIES=1
BAJA_RESEARCH_CIRCUIT_BREAKER_SECONDS=60
BAJA_RESEARCH_ACCESS_TIMEOUT_SECONDS=5
BAJA_RESEARCH_ACCESS_VALID_TTL_HOURS=168
BAJA_RESEARCH_ACCESS_INVALID_TTL_HOURS=24
BAJA_RESEARCH_ACCESS_TEMPORARY_TTL_HOURS=1
```

- `OPENALEX_API_KEY`: opcional; a busca tenta funcionar sem chave.
- `SEMANTIC_SCHOLAR_API_KEY`: opcional; enviada em `x-api-key` quando existe.
- `CROSSREF_MAILTO`: opcional e recomendado para identificar o cliente no
  pool educado da Crossref.
- `UNPAYWALL_EMAIL`: opcional; habilita a procura de cópia aberta por DOI.
- TTLs de acesso: PDFs válidos ficam sete dias em cache, links inválidos um
  dia e falhas temporárias uma hora.

As chaves nunca são registradas nos logs. O plugin também não lê a pasta de
sessão do WhatsApp.

## Testes unitários

```bash
cd /home/leofernandesc/BajaResearch
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

A suíte não depende permanentemente das APIs externas. Ela usa mocks para
HTTP 429, 5xx, páginas HTML, PDFs, redirects privados, falha parcial, cache e
serialização, além de cobrir DOI, títulos, merge, deduplicação, ranking, TCC
falso, foco técnico incorreto e EV em ordem invertida.

## Smoke test real

O smoke test usa as APIs públicas e um SQLite local ignorado pelo Git. A opção
`--query` pode ser repetida; `--technical-focus` alimenta o gate temático.

```bash
cd /home/leofernandesc/BajaResearch

.venv/bin/python smoke_test.py \
  --query "Baja SAE suspension optimization" \
  --query "projeto suspensao Mini Baja SAE" \
  --query "off-road vehicle suspension geometry" \
  --technical-focus "suspension geometry optimization" \
  --limit 3

.venv/bin/python smoke_test.py \
  --query "Baja SAE telemetry data acquisition sensors CAN" \
  --technical-focus "telemetry data acquisition sensors CAN" \
  --limit 3

.venv/bin/python smoke_test.py \
  --query "off-road CVT performance Baja SAE" \
  --technical-focus "CVT performance transmission" \
  --limit 3

.venv/bin/python smoke_test.py \
  --query "Baja SAE chassis finite element analysis" \
  --technical-focus "chassis finite element structural analysis" \
  --limit 3
```

Não existe opção para incluir conteúdo pago. Cada item impresso deve ter
`access=verified_pdf` e `PDF gratuito verificado`. Para priorizar artigos em
um teste explícito, use `--document-preference articles_first`; os gates de
tema e acesso continuam obrigatórios.

## Primeiro teste no terminal com Hermes

Após ativar o plugin, reinicie qualquer processo Hermes que já estava aberto.
Execute:

```bash
hermes chat \
  -s baja-research:baja-research \
  -q "Busque 3 trabalhos extensos sobre otimização de suspensão em Baja SAE e veículos off-road. Priorize TCCs e informe qualquer fonte indisponível."
```

A skill instrui o Hermes a produzir de três a cinco consultas complementares,
definir o foco técnico, responder no idioma do usuário e nunca completar
metadados de memória. No WhatsApp, a resposta lista no máximo cinco trabalhos
por padrão; a ferramenta aceita um limite final entre 1 e 20.

Para um teste de eletrônica que preserve a aplicação correta:

```bash
hermes chat \
  -s baja-research:baja-research \
  -q "Busque 3 TCCs ou dissertações sobre telemetria, aquisição de dados e sensores em Baja SAE ou veículos off-road."
```

## WhatsApp self-chat

BAJA Research não cria uma integração paralela. Ele usa o WhatsApp nativo do
Hermes.

1. Primeiro valide o comando de terminal acima.
2. Inicie o assistente nativo:

   ```bash
   hermes whatsapp
   ```

3. No Hermes v0.20.6 verificado, selecione `personal number (self-chat)` —
   opção `2` — e escaneie o QR em `WhatsApp > Configurações > Dispositivos
   conectados > Conectar dispositivo`.
4. Se o gateway já estiver instalado como serviço, recarregue o plugin:

   ```bash
   hermes gateway restart
   hermes gateway status
   ```

   Sem serviço instalado, mantenha `hermes gateway run` aberto em um terminal.
5. Envie uma mensagem para a conversa com você mesmo:

   ```text
   Use o BAJA Research para buscar 3 TCCs, dissertações ou trabalhos extensos sobre otimização de suspensão em Baja SAE e veículos off-road. Todos devem ter PDF completo gratuito e verificado. Informe fontes indisponíveis.
   ```

A resposta correta contém links diretos em `full_text_url`, não links de
compra ou landing pages. Não configure grupo, número dedicado, allowlist,
`require mention` ou VPS nesta fase.

## Cache e dados persistentes

Buscas idênticas e chamadas por fonte usam TTL independente. Evidências de PDF
também são persistidas para evitar downloads repetidos. O schema SQLite atual
é v2. Ao abrir um banco v1, o plugin cria uma cópia de segurança ao lado do
banco antes de migrar; ele não apaga o banco antigo silenciosamente.

No perfil local atual, o arquivo fica sob:

```text
~/.hermes/plugin-data/<id-do-plugin>/baja_research.sqlite3
```

Use `research_cache_stats` para ver contagens, última busca, schema, algoritmo
de ranking e disponibilidade observada das fontes.

## Troubleshooting

- **Plugin não aparece:** execute `hermes plugins doctor ... --ci`, confirme
  `hermes plugins list --plain --no-bundled` e reinicie o gateway.
- **O CLI mostra `Warning: Unknown toolsets: baja_research`:** no Hermes
  v0.20.6 verificado, o CLI pode validar o toolset dinâmico antes de concluir
  a descoberta assíncrona dos plugins. É um aviso de inicialização, não uma
  falha do BAJA Research: confirme com `hermes plugins doctor ... --ci`,
  `hermes tools list --platform cli` e
  `hermes tools list --platform whatsapp`. Não altere o core do Hermes nem
  habilite todos os toolsets apenas para ocultar esse aviso; as ferramentas
  são registradas e executadas normalmente depois da descoberta.
- **HTTP 429:** a fonte entra em circuit breaker temporário; resultados das
  outras fontes continuam válidos e o erro deve ser informado na resposta.
- **Vieram menos trabalhos que o pedido:** os demais falharam em tema, contexto
  Baja, filtro de EV ou validação do PDF. Isso é comportamento intencional.
- **Nenhum TCC:** use uma consulta técnica em português e outra em inglês. Não
  remova o contexto Baja/Formula/off-road e não transforme o pedido em um tema
  genérico.
- **Link abre no navegador, mas foi rejeitado:** login, cookie, HTML, 403, 429
  ou redirect inseguro não comprovam acesso anônimo estável. O plugin não
  apresenta esse link como gratuito.
- **Resultado antigo:** buscas finais e chamadas por fonte têm TTL de 24 horas.
  O smoke test força atualização; a ferramenta aceita `refresh_cache=true`.
- **Diagnóstico do gateway:** use
  `journalctl --user -u hermes-gateway.service -n 100 --no-pager` sem copiar ou
  publicar credenciais e arquivos da sessão WhatsApp.

## Limitações atuais

- O plugin verifica o acesso e os primeiros bytes do PDF, mas não interpreta o
  conteúdo integral; ainda não há processamento de PDFs ou RAG.
- A cobertura de TCCs depende dos índices Oasisbr/BDTD e dos padrões DSpace
  suportados. Repositórios com login, OAI-PMH indisponível ou endpoints
  desconhecidos são omitidos.
- Query expansion continua principalmente sob responsabilidade do Hermes/LLM;
  o plugin adiciona somente salvaguardas de recuperação e aplica gates rígidos.
- O ranking mede adequação à consulta, não qualidade metodológica definitiva.
  A equipe ainda deve avaliar método, dados, resultados e aplicabilidade.
- APIs externas podem mudar, limitar ou ficar temporariamente indisponíveis.
- Não há frontend, servidor web, autenticação própria, biblioteca
  compartilhada ou sincronização entre computadores.

## Roadmap — não implementado

1. **Fase 2:** número dedicado, WhatsApp bot mode, grupo BAJA Research, group
   allowlist, `require mention` e Oracle VPS.
2. **Fase 3:** biblioteca compartilhada, trabalhos salvos, usuários/áreas e
   PostgreSQL somente se necessário.
3. **Fase 4:** processamento de PDFs, RAG, documentação interna, regulamentos,
   normas e datasheets.
4. **Fase 5:** monitoramento de novos trabalhos, notificações e recomendações
   personalizadas por área.
