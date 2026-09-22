# BAJA Research

BAJA Research é um plugin standalone para o Hermes Agent que encontra
literatura acadêmica aplicável a equipes Baja SAE. A versão 0.4.0 foi desenhada
para uso local, com ou sem Hermes, e no WhatsApp self-chat, com prioridade para TCCs,
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
- o plugin baixou o PDF inteiro anonimamente, verificou `%PDF-`, `%%EOF`,
  estrutura/páginas e registrou tamanho e hash SHA-256. PDF truncado,
  criptografado ou login HTML não é recomendado.

O usuário não precisa escrever "Baja", "PDF completo" ou "gratuito" no pedido.
Exemplos suficientes: `Busque 3 TCCs sobre suspensão` e `artigos sobre LoRa`.
No primeiro, o tipo TCC é obrigatório: dissertações não ocupam suas vagas.
No segundo, "artigos" significa trabalhos acadêmicos em geral, não um filtro
que descarta TCCs. O filtro estrito de artigos só vale quando o usuário pede
*somente artigos de periódico/conferência*.
O modelo não consegue desativar sozinho o filtro de EV: a exceção só é aceita
quando a pergunta original menciona explicitamente essa tecnologia.

DOI, selo open access, página de editora, landing page de repositório e HTTP
200 não são prova suficiente de acesso gratuito. O plugin retorna menos
resultados quando necessário, em vez de completar a lista com material pago,
fora do tema ou com link não verificado.

O Google Scholar não é consultado nem raspado. O MVP usa APIs e repositórios
com interfaces próprias, evitando uma integração frágil e sem API pública
adequada para este fluxo.

## Arquitetura

```text
CLI local ou Hermes (chat / WhatsApp self-chat)
             -> motor ResearchService
             -> rota 1: Oasisbr + UFSCar/DSpace + OpenAlex + OpenAIRE
             -> normalização, deduplicação, gates, PDF completo verificado
             -> se faltarem resultados válidos: rota 2
                BDTD + Semantic Scholar + arXiv API + consultas alternativas
             -> fallback FTS5 de metadados locais, com PDF revalidado
             -> ranking e resposta curta + estado de cada fonte
             -> SQLite local (diretório de dados do Hermes ou XDG no CLI)
```

O fallback depende de **resultados relevantes e PDFs aprovados**, não do
número bruto de hits da API. arXiv usa a API Atom oficial, com ritmo de uma
solicitação a cada três segundos, e é apenas suplemento de preprints: não é
fonte primária de TCCs. Não há scraping intenso de um único site.

Na versão local verificada, Hermes Agent v0.20.6, a API pública usada é
`register(ctx)`, `ctx.register_tool(...)`, `ctx.register_skill(...)`,
`ctx.register_hook("pre_llm_call", ...)` e
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

1. **Oasisbr e UFSCar/DSpace direto**: descoberta de TCCs, monografias,
   dissertações e teses brasileiras; a fonte direta reduz perda por agregador.
2. **BDTD**: complemento de trabalhos longos quando a primeira rota não fornece
   cobertura verificada suficiente.
3. **OpenAlex**: descoberta acadêmica global, incluindo artigos e metadados de
   acesso aberto.
4. **OpenAIRE**: complemento global de repositórios, com teses/dissertações e
   links candidatos para PDFs; cada link passa pela mesma checagem de bytes.
5. **Semantic Scholar e arXiv API**: fallback global, citações, enriquecimento,
   relacionados e preprints abertos, respectivamente. arXiv não substitui
   repositórios de TCCs.
6. **Crossref**: resolução e enriquecimento bibliográfico por DOI. Não é usado
   como fonte primária de candidatos, pois licença ou link da editora não
   garantem PDF gratuito.
7. **Unpaywall**: resolvedor opcional de cópias abertas por DOI. Toda URL ainda
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
contexto Baja/Formula/off-road. A correspondência com o foco técnico é testada
separadamente das consultas expandidas: termos de recuperação como
`undergraduate` não podem justificar um trabalho fora do tema. Dentro de cada
classe documental, o score usa
52% de relevância técnica, 25% de contexto de aplicação, 8% de relevância da
fonte, 5% de completude, 4% de confirmação multi-fonte, 4% de citações
log-normalizadas e 2% de recência. Assim, citações e novidade não dominam a
lista. Contexto Baja/Formula direto pontua mais que uma aplicação off-road
transferível. A ordenação padrão coloca TCCs, depois outros trabalhos longos,
depois artigos; um título
contendo apenas a palavra “TCC” não é classificado como TCC.

## Pré-requisitos

- Hermes Agent instalado e disponível como `hermes`;
- Python 3.11 ou superior;
- `httpx>=0.27,<1` no Python usado pelo Hermes;
- `pypdf>=5,<7` no Python usado pelo Hermes;
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

Se o ambiente do Hermes não tiver as dependências (a instalação local verificada
não tinha `pip` no venv):

```bash
~/.hermes/hermes-agent/venv/bin/python -m ensurepip --upgrade
~/.hermes/hermes-agent/venv/bin/python -m pip install 'httpx>=0.27,<1' 'pypdf>=5,<7'
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
BAJA_RESEARCH_GLOBAL_TIMEOUT_SECONDS=60
BAJA_RESEARCH_MAX_RETRIES=1
BAJA_RESEARCH_CIRCUIT_BREAKER_SECONDS=60
BAJA_RESEARCH_ACCESS_TIMEOUT_SECONDS=20
BAJA_RESEARCH_MAX_PDF_MB=40
BAJA_RESEARCH_MAX_SEARCH_PDF_MB=160
BAJA_RESEARCH_ACCESS_VALID_TTL_HOURS=1
BAJA_RESEARCH_ACCESS_INVALID_TTL_HOURS=24
BAJA_RESEARCH_ACCESS_TEMPORARY_TTL_HOURS=1
```

- `OPENALEX_API_KEY`: opcional; a busca tenta funcionar sem chave.
- `SEMANTIC_SCHOLAR_API_KEY`: opcional; enviada em `x-api-key` quando existe.
- `CROSSREF_MAILTO`: opcional e recomendado para identificar o cliente no
  pool educado da Crossref.
- `UNPAYWALL_EMAIL`: opcional; habilita a procura de cópia aberta por DOI.
- TTLs de acesso: PDFs válidos ficam uma hora em cache, links inválidos um
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

## Busca local sem Hermes

O motor é independente da interface de mensagens. Para testar a mesma busca
com uma única frase, sem depender do LLM ou do gateway:

```bash
cd /home/leofernandesc/BajaResearch
.venv/bin/baja-research search "3 artigos sobre LoRa"
.venv/bin/baja-research search "3 TCCs sobre suspensão" --refresh-cache
.venv/bin/baja-research stats
```

`--json` mostra metadados e evidência técnica (páginas, bytes, SHA-256). O
CLI usa `~/.local/share/baja-research/baja_research.sqlite3` por padrão
(ou `XDG_DATA_HOME`), separado do banco do plugin Hermes. `--db` permite
apontar para outro SQLite. O código de saída `1` significa nenhum trabalho
aprovado pelos filtros; não significa que a API não respondeu.

## Smoke test real

O smoke test usa as APIs públicas e um SQLite local ignorado pelo Git. A opção
`--query` pode ser repetida; foco, tipo TCC/artigo e contexto Baja são
inferidos quando omitidos. Use `--refresh-cache` para forçar chamadas reais.

```bash
cd /home/leofernandesc/BajaResearch

.venv/bin/python smoke_test.py \
  --query "3 TCCs sobre suspensão" \
  --limit 3

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
    -q "Busque 3 TCCs sobre suspensão"
```

A skill e o hook ajudam o Hermes a usar a ferramenta em uma chamada inicial,
responder no idioma do usuário e nunca completar metadados de memória. O
plugin insere o contexto Baja, escolhe consultas curtas em português/inglês e
exige PDF completo gratuito verificado mesmo quando o pedido não diz isso.
No WhatsApp, a resposta lista no máximo cinco trabalhos
por padrão; a ferramenta aceita um limite final entre 1 e 20.

Para um teste de eletrônica que preserve a aplicação correta:

```bash
hermes chat \
  -s baja-research:baja-research \
  -q "Busque 3 TCCs sobre telemetria e aquisição de dados"
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
   Busque 3 TCCs sobre suspensão
   ```

A resposta correta contém links diretos em `full_text_url`, não links de
compra ou landing pages. Não configure grupo, número dedicado, allowlist,
`require mention` ou VPS nesta fase.

## Cache e dados persistentes

Buscas idênticas e chamadas por fonte usam TTL independente. Evidências de PDF
também são persistidas para evitar downloads repetidos. Buscas vazias expiram
em 30 minutos, não em 24 horas. O schema SQLite atual é v3 e inclui índice
FTS5 de metadados locais. Ao abrir banco v1/v2, o plugin cria uma cópia
consistente com a API de backup do SQLite ao lado do banco antes de migrar;
ele não apaga o banco antigo silenciosamente. O FTS5 não prova acesso: cada
resultado recuperado localmente passa de novo pelo gate de PDF.

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
- **Pedido curto de artigos retornou poucos resultados:** artigos pagos ou
  genericamente sobre eletrônica são excluídos. `artigos sobre LoRa` já inclui
  TCCs Baja/Formula relevantes; apenas `somente artigos de periódico` ativa o
  filtro estrito.
- **Nenhum TCC:** nenhum trabalho do tipo pedido passou simultaneamente pelos
  filtros de contexto e PDF. O plugin já tenta variações português/inglês;
  tente novamente mais tarde ou reformule o assunto técnico, se desejar.
- **Link abre no navegador, mas foi rejeitado:** login, cookie, HTML, 403, 429
  ou redirect inseguro não comprovam acesso anônimo estável. O plugin não
  apresenta esse link como gratuito.
- **Resultado antigo:** buscas finais e chamadas por fonte têm TTL de 24 horas
  quando positivas; resultados vazios expiram em 30 minutos. Use
  `--refresh-cache` no CLI/smoke test ou `refresh_cache=true` na ferramenta.
- **Diagnóstico do gateway:** use
  `journalctl --user -u hermes-gateway.service -n 100 --no-pager` sem copiar ou
  publicar credenciais e arquivos da sessão WhatsApp.

## Limitações atuais

- O plugin baixa e valida a estrutura do PDF inteiro, mas não lê semanticamente
  seu conteúdo. Relevância e extensão são estimadas por metadados e número de
  páginas; não há análise metodológica automática nem RAG.
- A cobertura de TCCs depende dos índices Oasisbr/BDTD e dos padrões DSpace
  suportados, complementados pelo OpenAIRE. Repositórios com login, OAI-PMH indisponível ou endpoints
  desconhecidos são omitidos.
- arXiv entra só no fallback de preprints e pode não ter um trabalho sobre o
  tema específico. Nenhuma fonte única cobre todos os TCCs de Baja.
- O plugin executa um plano pequeno de consultas por fonte mesmo sem Hermes;
  o LLM pode fornecer consultas técnicas adicionais, mas não precisa fazê-lo
  para pedidos curtos.
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
