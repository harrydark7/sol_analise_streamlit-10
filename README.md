# SOL / Solfácil — Sistema de Análise de Carteira

Sistema em **Python + Streamlit** para substituir o controle em Excel da carteira SOL/Solfácil.

A aplicação importa e cruza as bases:

- **Base**
- **Pagamentos**
- **Acionamento**
- **DePara de Ocorrências**
- **DePara de Faixa de Atraso / Taxa de H.O.**
- **Metas**

A chave principal do sistema é o **ID_FIN**, que representa o contrato. O CPF/CNPJ é usado para consolidação e geração de arquivo único para CRM, mas não substitui o ID_FIN na análise operacional.

---

## 1. Como executar

### Windows

1. Instale Python 3.11 ou superior.
2. Abra o Prompt de Comando dentro da pasta do projeto.
3. Execute:

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

Ou dê duplo clique em:

```text
run_app.bat
```

---

## 2. Estrutura do projeto

```text
sol_analise_streamlit/
├── app.py
├── requirements.txt
├── run_app.bat
├── README.md
├── data/
│   ├── solfacil.db
│   └── templates/
│       ├── depara_ocorrencias.csv
│       ├── depara_atraso.csv
│       ├── depara_operadores.csv
│       └── metas.csv
├── scripts/
│   └── import_snapshot.py
└── src/
    ├── config.py
    ├── exporters.py
    ├── io_utils.py
    ├── metrics.py
    ├── storage.py
    ├── transformations.py
    ├── utils.py
    └── validators.py
```

---

## 3. Telas disponíveis

- **Dashboard Geral**: KPIs de carteira, valores, pagamentos, H.O., funil e sem tentativa, com filtros separados por data da Base de Clientes, Base de Pagamentos e Base de Acionamentos.
- **Importar arquivos**: importação das bases apartadas ou de um Excel completo com abas.
- **Funil de Acionamento**: TENTATIVA > ALO > CPC > CPCA > ACORDO por célula, faixa, fundo ou carteira.
- **Pagamentos e H.O.**: valor pago, H.O., evolução diária e visão por faixa.
- **Produtividade por Operador**: produção por responsável.
- **Novas Entradas**: identifica novos ID_FIN, removidos, mantidos e mudança de faixa entre bases diárias.
- **CPF Único CRM**: gera base consolidada por CPF/CNPJ para importação no CRM.
- **Parâmetros / DePara**: consulta e atualização dos DeParas.
- **Exportações e Logs**: exporta relatório Excel completo e consulta logs de importação.

---

## 4. Filtros por data de cada base importada

A partir desta versão, toda importação de **Base**, **Pagamentos** e **Acionamento** grava uma coluna chamada:

```text
DATA_IMPORTACAO
```

Essa data é definida no campo **Data de referência/importação do arquivo** durante a importação.

Na **Dashboard Geral** e nas demais telas operacionais, o menu lateral permite selecionar separadamente:

- **Data da base de clientes**
- **Data da base de pagamentos**
- **Data da base de acionamentos**

Com isso, é possível cruzar, por exemplo:

- Base de clientes do dia 15/06;
- Pagamentos importados no dia 16/06;
- Acionamentos importados no dia 16/06.

A Base de Clientes continua usando **DATA_BASE** como data principal da carteira. Já Pagamentos e Acionamentos usam **DATA_IMPORTACAO** para representar o snapshot importado.

Ao usar a opção **Substituir mesma data da base**, o sistema agora substitui os registros da mesma data de referência/importação também para Pagamentos e Acionamentos.

---

## 5. Regras principais implementadas

### ID_FIN como chave principal

O sistema usa **ID_FIN** como chave principal de cruzamento entre as bases.

Mesmo que o mesmo CPF/CNPJ tenha mais de um contrato, cada **ID_FIN** é tratado como contrato diferente.

### Novas entradas

Uma nova entrada é todo **ID_FIN** que aparece na base atual e não existia na base anterior.

Também são geradas as visões:

- contratos novos;
- contratos removidos;
- contratos mantidos;
- contratos com alteração de faixa de atraso.

### CPF/CNPJ único para CRM

A tela **CPF Único CRM** consolida os contratos por CPF/CNPJ e informa:

- quantidade de contratos;
- lista de ID_FIN;
- VPL total;
- valor bruto total;
- saldo em atraso;
- maior atraso;
- menor atraso;
- contrato principal;
- faixa principal;
- célula principal;
- indicativo de pagamento;
- indicativo de acionamento;
- último acionamento;
- última finalização;
- último responsável.

### H.O.

O H.O. é calculado a partir da taxa cadastrada no DePara de atraso:

```text
H.O. = ValorPagamento × Taxa de H.O.
```

### Funil

O sistema classifica a finalização do acionamento conforme o DePara de ocorrências:

- TENTATIVA
- ALO
- CPC
- CPCA
- ACORDO

Depois soma os eventos por ID_FIN e monta o funil.

---

## 6. Layouts esperados

### Base

Campos mínimos:

- ID ou ID_FIN
- CPF/CNPJ

Campos recomendados:

- FUNDO
- TIPO DO CLIENTE
- CCB
- DIAS DE ATRASO
- CARTEIRA
- VALOR BRUTO
- VPL
- SALDO EM ATRASO
- PARCELAS EM ATRASO
- PARCELAS PAGAS
- ETAPA ATUAL
- CARENCIA
- STATUS COBRANCA
- NOVO?
- cod_produt
- SITU_COB
- Acordo
- VPL Convertido
- Data da Base

### Pagamentos

Campos mínimos:

- ID_FIN
- DataVencimento
- DataPagamentoBoleto
- ValorPagamento

Campos recomendados:

- CCB
- Fundo
- Parcela
- DiasAtraso
- CARTEIRA

### Acionamento

Campos mínimos:

- ID_FIN
- FINALIZACAO
- DATA_ACIONAMENTO

Campos recomendados:

- COD_HISTO
- DATA_AGENDADA
- MOTIVO_ATRASO
- CARTEIRA_DISTRIBUIDA
- RESPONSAVEL
- TELEFONE_ACIONADO
- TIPO_ACIONAMENTO
- CANAL_ACIONAMENTO
- COMENTARIO_ACIONAMENTO

---

## 7. Importação de um Excel completo

Se o arquivo possuir as abas:

- `Base`
- `Pagamentos`
- `Acionamento`
- `DePara`

use a opção:

```text
Arquivo completo com abas Base/Pagamentos/Acionamento/DePara
```

O sistema tentará importar todas as abas automaticamente.

---

## 8. Importação inicial por script

Também é possível importar um Excel diretamente pelo terminal:

```bash
python scripts/import_snapshot.py "SOLFACIL - 15.06.xlsx" --data-base 2026-06-15
```

---

## 9. Banco de dados

O banco local fica em:

```text
data/solfacil.db
```

Pode ser apagado para reiniciar a aplicação do zero.

---

## 10. Observações importantes

- O sistema já vem com DeParas extraídos do modelo analisado.
- As fórmulas do Excel foram transformadas em regras de cálculo em Python.
- A aba Resumo foi convertida em dashboards e agrupamentos dinâmicos.
- As exportações são feitas em Excel, com várias abas tratadas.

---

## 11. Regras de competência da base e régua de receita

A versão atual aplica uma regra de competência antes de calcular funil, pagamentos e receita:

1. A **Base de Clientes** define o mês de competência da análise.
2. Acionamentos só são considerados quando:
   - pertencem ao mesmo mês da base selecionada;
   - são posteriores ou iguais à primeira entrada do `ID_FIN` na carteira.
3. Pagamentos só são considerados quando:
   - pertencem ao mesmo mês da base selecionada;
   - são posteriores ou iguais à primeira entrada do `ID_FIN` na carteira.
4. A primeira entrada do contrato é calculada pela menor `DATA_BASE` histórica do `ID_FIN`.

### Régua de receita / comissionamento

Para cada pagamento elegível, o sistema cria as seguintes colunas:

- `TIPO_RECEITA`
- `COMISSIONAVEL`
- `VALOR_PAGO_COMISSIONAVEL`
- `HO_COMISSIONAVEL`
- `VALOR_PAGO_NAO_COMISSIONAVEL`
- `HO_NAO_COMISSIONAVEL`
- `QTD_ACIONAMENTOS_ANTES_PGTO`
- `QTD_ACORDOS_ANTES_PGTO`
- `ULTIMA_DATA_ACIONAMENTO_ANTES_PGTO`
- `ULTIMA_FINALIZACAO_ANTES_PGTO`
- `ULTIMO_RESPONSAVEL_ANTES_PGTO`
- `STATUS_VENCIMENTO`

Classificações possíveis:

- `ACORDO_BL_ANTES_DO_PAGAMENTO`: pagamento com ocorrência de acordo registrada antes ou na data do pagamento. Conta como receita comissionável.
- `INDIRETO_COM_ACIONAMENTO_ANTES_DO_PAGAMENTO`: pagamento sem acordo, mas com acionamento válido antes ou na data do pagamento. Conta como receita comissionável.
- `DIRETO_SEM_ACIONAMENTO_OU_ACORDO`: pagamento sem evidência de acionamento ou acordo prévio. Não conta como receita comissionável.
- `PAGAMENTO_ANTES_DO_VENCIMENTO`: pagamento com data de pagamento anterior ao vencimento. Fica fora da régua de receita.
- `SEM_DATA_PAGAMENTO_OU_VENCIMENTO`: pagamento sem data suficiente para validação. Fica fora da régua de receita.

A tela **Régua de Receita** apresenta o resumo por tipo, célula, faixa e o detalhe de todos os pagamentos classificados.

## Atualização v4 — filtros mensais e correções de dashboard

Esta versão inclui:

- Filtro por mês para Base de Clientes, Pagamentos e Acionamentos.
- Opção de selecionar todas as importações do mês em cada base.
- Quando várias bases de clientes são selecionadas, o dashboard usa o último snapshot de cada ID_FIN dentro do período, evitando duplicidade.
- Correção do erro do Plotly: `cannot process wide-form data with columns of different type`.
- Correção da coluna ACORDO, que podia vir como texto SIM/NAO do Excel e concatenar valores na tabela.
- Percentuais exibidos como percentual brasileiro, por exemplo `12,34%`.
- Valores monetários exibidos em formato brasileiro, por exemplo `R$ 164.338,78`.
- Dashboard Geral reorganizado para evitar corte dos valores nos cards.
- Aba Novas Entradas com resumo dia a dia do mês: qtd. anterior, qtd. atual, entradas, saídas, mantidos, delta e VPL.

Para rodar:

```powershell
python -m streamlit run app.py
```

## Atualização v5 - Funil acumulado, reentradas e devoluções

Esta versão ajusta a regra operacional da carteira SOL/Solfácil:

- A base atual considerada nos dashboards é sempre a última base selecionada dentro do mês.
- O funil é acumulado: ACORDO conta também como CPCA, CPC, ALO e TENTATIVA; CPCA conta também como CPC, ALO e TENTATIVA; CPC conta também como ALO e TENTATIVA; ALO conta também como TENTATIVA.
- Os acionamentos considerados no funil e na régua de receita são somente registros com RESPONSAVEL preenchido, excluindo registros sistêmicos/automáticos comuns.
- Acionamentos duplicados são removidos usando COD_HISTO quando disponível e, principalmente, a combinação ID_FIN + DATA_ACIONAMENTO + RESPONSAVEL + FINALIZACAO + TIPO_OCORRENCIA.
- A tela Novas Entradas passa a identificar NOVA_ENTRADA, REENTRADA, DEVOLVIDO, SAIDA_TEMPORARIA e MANTIDO.
- Pagamentos continuam usando a taxa de H.O. conforme dias em atraso, faixa de atraso e célula da tabela DePara vigente.

## Atualização v6 — desempenho e regra de receita

Esta versão corrige a lentidão da Dashboard Geral e reforça a regra financeira de comissionamento:

- O cálculo de receita/H.O. usa sempre `VALOR_PAGAMENTO` da base de pagamentos.
- `SALDO_VPL` e `VPL_CONVERTIDO` ficam somente para carteira, produção e meta do cliente.
- `HO = VALOR_PAGAMENTO × TAXA_HO`, conforme faixa/célula de atraso da tabela DePara vigente.
- O funil de acionamento é acumulado: ACORDO também conta como CPCA, CPC, ALO e TENTATIVA.
- A régua de receita foi otimizada para não travar com bases maiores.
- A carga das bases foi cacheada e o cache é invalidado automaticamente quando o banco local é atualizado.

---

## 7. Atualização v8 — fluxo diário consolidado SOL

Esta versão considera o fluxo operacional informado para julho/2026:

### Bases de clientes

- Cada arquivo de distribuição diária é um snapshot da carteira no dia.
- A **Base Ativa do mês** é sempre a última base diária importada dentro do mês/data de referência.
- A **Base Total do mês** considera todos os `ID_FIN` que passaram pela carteira no mês, incluindo contratos que depois foram devolvidos.
- A **Base Congelada** é uma nova importação específica para a base da meta do cliente. Caso ainda não exista uma base congelada importada, o sistema usa temporariamente o primeiro snapshot do mês como fallback visual.
- **Clientes Entrantes** são os `ID_FIN` que aparecem pela primeira vez a partir do dia 02 do mês.

### Acionamentos

- A base de finalização/acionamento recebida diariamente é tratada como **consolidada do mês**.
- Na importação, o sistema remove duplicidades por:
  - `COD_HISTO`
  - `ID_FIN`
  - `DATA_ACIONAMENTO`
  - `FINALIZACAO`
  - `RESPONSAVEL`
- Como o arquivo é consolidado, a dashboard usa por padrão todos os acionamentos únicos com `DATA_ACIONAMENTO` dentro do mês da base, e não apenas a data de importação.
- O funil é acumulado:
  - `ACORDO` também conta como `CPCA`, `CPC`, `ALO` e `TENTATIVA`.
  - `CPCA` também conta como `CPC`, `ALO` e `TENTATIVA`.
  - `CPC` também conta como `ALO` e `TENTATIVA`.
  - `ALO` também conta como `TENTATIVA`.

### Pagamentos

- A última base de pagamentos importada no mês é usada como **controle oficial de pagamento**.
- As bases anteriores continuam guardadas para auditoria, permitindo identificar `ID_FIN` que tinham pagamento em uma base anterior e sumiram da última base.
- O valor total de recebimento vem sempre de `VALOR_PAGAMENTO`, removendo duplicidades financeiras.
- O cálculo de H.O./receita não usa Saldo VPL como base de comissão.
- O cálculo financeiro segue:

```text
Valor pago do contrato = soma de VALOR_PAGAMENTO por ID_FIN
H.O. do contrato = Valor pago do contrato × TAXA_HO_CONTRATO
```

- A `TAXA_HO_CONTRATO` é obtida pela faixa/célula vigente conforme dias de atraso e DePara de H.O.
- O Saldo VPL continua sendo usado apenas para carteira, produção e meta do cliente.

### Visões adicionadas

Na Dashboard Geral foram adicionados indicadores de composição da carteira no mês:

- Base ativa do mês
- Base total do mês
- CPFs total do mês
- Base congelada
- Clientes entrantes
- Devolvidos no mês



## Atualização V9 — ajustes de pagamentos, H.O. e funil

Esta versão inclui:

- DePara de ocorrências no novo formato `MOTIVO_ATRASO + TENTATIVA/ALO/CPC/CPCA/ACORDO`.
- DePara de operadores com origem do acionamento e supervisora.
- Tabela de honorários atualizada por faixa de atraso.
- Filtro de `CANAL_ACIONAMENTO` na lateral.
- Pagamentos/H.O. conciliados pela última base oficial de pagamentos do mês.
- Separação de valor pago com/sem acionamento BL dentro do mês.
- H.O. calculado sobre `VALOR_PAGAMENTO`, nunca sobre Saldo VPL.
- Linhas de finalização sem classificação passam a contar como `TENTATIVA`.

Veja o arquivo `VALIDACAO_V9.md` para os totais conferidos com as bases anexadas.
