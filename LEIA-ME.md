# Licitações ES: app para o celular

Mostra no celular as licitações **abertas no Espírito Santo** que combinam com os seus ramos:

1. Manutenção predial
2. Manutenção em subestação
3. Manutenção elétrica
4. Materiais elétricos
5. Materiais de manutenção predial
6. Transformadores

**Como funciona:** de hora em hora (7h às 19h, segunda a sexta; uma vez por dia no fim de semana) um robô no GitHub consulta o PNCP, separa o que interessa e atualiza uma página que você abre no celular como se fosse um app. Não precisa de servidor, não tem custo e não precisa de senha do PNCP.

Na tela você tem: abas *Relevantes / Todas / Salvas*, filtro por ramo, "Encerra em 7 dias", "Só novas", busca, ordenação, botão para abrir o edital no PNCP e no sistema de origem, e botão para enviar a licitação por WhatsApp.

---

## Instalação (uns 10 minutos, uma vez só)

1. **Conta no GitHub.** Crie em github.com (grátis).
2. **Novo repositório.** Botão "+" no topo > *New repository*. Nome: `licitacoes-es`. Marque **Public** (o GitHub Pages grátis exige repositório público; os dados são públicos do PNCP e só as suas palavras-chave ficam visíveis). *Create repository*.
3. **Enviar os arquivos.** Na página do repositório, clique em *uploading an existing file* e arraste **todo o conteúdo** da pasta deste pacote (`coletor.py`, `config.json`, `modelo.html`, pasta `docs` e pasta `.github`). Clique em *Commit changes*.
   - Se a pasta `.github` não subir: *Add file > Create new file*, digite o nome `.github/workflows/atualizar.yml`, cole o conteúdo do arquivo de mesmo nome e faça o *Commit*.
4. **Ligar a página.** *Settings > Pages > Build and deployment > Source:* escolha **GitHub Actions**.
5. **Rodar a primeira vez.** Aba *Actions > Atualizar licitações > Run workflow*. Se pedir, aceite habilitar os workflows. Em 1 a 2 minutos fica verde.
6. **Abrir no celular.** O endereço é `https://SEU-USUARIO.github.io/licitacoes-es/` (aparece também no resumo da execução). Abra no celular e use *Adicionar à tela inicial* (Chrome: menu ⋮; iPhone/Safari: botão Compartilhar).

Depois disso tudo é automático.

## Avisos no celular quando aparecer licitação nova (opcional)

1. Instale o app **ntfy** (Android/iPhone) e toque em **+** para assinar um tópico com um nome longo e difícil de adivinhar, por exemplo `licit-es-k7x93qmz52`.
2. No GitHub: *Settings > Secrets and variables > Actions > New repository secret*. Nome: `NTFY_TOPIC`. Valor: o nome do tópico.

Quem souber o nome do tópico lê os avisos, então não divulgue. Os avisos só incluem licitações relevantes **novas** desde a última atualização (no máximo 5 avisos individuais por vez; acima disso vai um resumo).

## Ajustar palavras-chave

Abra `config.json` no GitHub (lápis de editar), altere e faça *Commit*. A próxima atualização já usa a nova lista (ou rode manualmente em *Actions*).

- `fortes`: termos que sozinhos já indicam o ramo (3 pontos).
- `fracas`: termos de apoio (1 ponto cada). Uma licitação entra no ramo com 2 pontos ou mais. Com apenas **um** termo fraco ela vai para *Todas* marcada como **"Possível"** (um indício só: confira).
- `apoio`: palavras comuns como "cabo", "fio", "tomada", "led" (1 ponto). Elas só contam junto com outro termo do ramo, para não trazer cabo de vassoura ou tomada de preços.
- `excluir`: derruba resultados que só tenham termos fracos (ex.: "veículo", "software", "energia elétrica").
- Termos que começam com `re:` são expressões regulares (em minúsculas e sem acento). Servem para achar combinações, como "materiais **e componentes** elétricos" ou "manutenção **preventiva e corretiva de** prédios".
- Acentos e maiúsculas não importam, e "transformador" também acha "transformadores". Termos de até 4 letras valem só como palavra inteira ("led", "fio").
- Em cada cartão aparece *"Apareceu por: ..."* (ou *"Apareceu pelos itens da licitação: ..."*) para você ver por que ele foi selecionado e ajustar a lista.

## Aba Busca (todas as abertas no ES)

- Lista **todas** as licitações abertas no ES que o app leu (PNCP e portais), de qualquer assunto, e deixa buscar por palavra-chave.
- A busca ignora maiúsculas e acentos e acha pelo **começo da palavra** ("eletric" acha elétrica; "elevador" acha elevadores). Palavras de até 3 letras valem só inteiras ("ar" não acha "para").
- Com duas palavras ou mais, escolha **Todas as palavras** (todas precisam aparecer) ou **Qualquer palavra**.
- Filtro por **município**, "Encerra em 7 dias", "Só novas" e "Outros portais" funcionam junto. A palavra aparece destacada no cartão.
- A busca olha objeto, órgão, cidade, número e os itens da licitação quando já foram lidos. As últimas buscas ficam em **Recentes**.

## Selecionar licitações e enviar por WhatsApp para clientes

1. Em qualquer aba de lista (Relevantes, Todas, Busca ou Salvas), toque em **Selecionar para enviar**. Cada cartão ganha um botão **Selecionar**.
2. Para mandar um nicho inteiro: toque no filtro do ramo (ou faça uma busca) e depois em **Marcar as N da lista**. A seleção continua se você trocar de aba, então dá para juntar licitações de filtros diferentes.
3. Toque em **Enviar no WhatsApp**. Abre uma mensagem pronta (ramo, objeto, órgão, cidade, prazo e link de cada licitação, em ordem de prazo). Você pode editar o texto, por exemplo para incluir uma saudação.
4. Toque em **Abrir no WhatsApp**, escolha o cliente (ou grupo) e envie. Para mandar a outro cliente, volte ao app e toque no botão de novo.
5. Se a lista for grande, a mensagem é dividida em partes (o WhatsApp limita o tamanho do link); envie uma de cada vez, na ordem. Há também **Copiar texto** e **Compartilhar**.
6. Toque em **Limpar** para recomeçar com outro nicho, ou desligue o botão Selecionar.

O app não manda mensagens sozinho: quem envia é você, pelo seu WhatsApp. A lista de clientes não fica guardada no app.

## Abas Relevantes e Todas

- **Relevantes**: licitações que atingiram a pontuação mínima em algum dos seus ramos.
- **Todas**: tudo o que está aberto no ES e que o app leu, de qualquer assunto. Para ver só o seu nicho (Relevantes + as marcadas "Possível"), toque em **Só o meu nicho**, ou escolha um ramo nos filtros.
- Todo cartão do PNCP tem **Baixar edital** e **Itens e anexos**. Quando o app não trouxe esses dados prontos, ele tenta buscá-los no PNCP na hora do toque.
- **Baixar edital no app instalado:** o celular pode travar ao abrir PDF dentro do app. Por isso, no app instalado o botão abre um painel com **Abrir no navegador**, **Copiar link** e **Compartilhar**.

## Outros portais que alimentam a lista

Além do PNCP, o robô lê (sem login) e coloca na mesma lista, com a etiqueta do portal e o filtro **Outros portais**:

- **Portal de Compras Públicas** (portaldecompraspublicas.com.br): processos do ES com propostas abertas ou prestes a abrir. O portal mostra o objeto cortado em cerca de 250 letras, então o app classifica só pelo que aparece; abra o processo no portal para ler inteiro. Quando o mesmo processo também está no PNCP, aparece só a versão do PNCP (que tem edital e itens).
- **SEST SENAT**, **FINDES / SENAI / SESI** e **SENAC ES**.

A aba **Fontes** mostra, para cada portal, quantas licitações entraram, quando foi a última leitura e o motivo se alguma leitura falhou (o app continua mostrando a última leitura que deu certo). Para desligar um portal, acrescente em `config.json`, por exemplo: `"fontes_extra": {"pcp": false}` (as chaves são `pcp`, `sestsenat`, `findes`, `senac_es`).

## O que o app cobre e o que não cobre

- **Cobre** o que é publicado no PNCP, que é obrigatório para a Lei 14.133/2021. No teste apareceram publicações vindas do Compras.gov.br, Licitanet e Portal de Compras Públicas.
- **Não cobre (só pelo PNCP):** estatais e concessionárias com regulamento próprio, e municípios com até 20 mil habitantes que ainda não integraram ao PNCP (prazo até 1º/04/2027). Parte disso é coberta pelos outros portais acima.
- A busca olha o **objeto e as informações complementares** do edital e, quando o objeto é genérico ("aquisição de materiais"), também os **itens** da licitação (lidos no PNCP, no máximo 150 novas por atualização). O que ainda escapar aparece na aba *Todas* (ou na aba *Busca*).
- Valor "não informado" aparece quando o órgão informa 0, 1 real ou sigilo.
- O app ajuda a **achar** oportunidades. Prazos, exigências e habilitação devem ser conferidos sempre no edital.

## Se algo der errado

- **Execução falha com "HTTP 403":** o PNCP recusou o acesso vindo do GitHub. Rode no seu computador (no Brasil): instale o Python 3.9+, abra o terminal na pasta e digite `python coletor.py`. A página sai em `docs/index.html`.
- **Aparece "dados antigos":** a atualização automática parou. Veja a aba *Actions* no GitHub para ver o erro.
- **Quer outro estado:** troque `"uf": "ES"` em `config.json`.

## Se o app ficar com a data antiga

Às vezes a coleta funciona, mas o GitHub falha ao **publicar** o site (erro "No artifacts named github-pages were found"): os dados novos ficam salvos no repositório e o app continua mostrando a versão anterior. O arquivo `atualizar.yml` desta versão espera alguns segundos e tenta publicar até 3 vezes. Se mesmo assim falhar, abra *Actions*, entre na execução com o X vermelho e toque em **Re-run failed jobs**.
