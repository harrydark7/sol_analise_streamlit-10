# Validação V10 — Célula Entrantes

## Ajuste solicitado

Foi criada a visão/célula **Entrantes** para identificar todos os `ID_FIN` cuja primeira entrada no mês ocorreu a partir do dia 02 do mês de referência.

## Regra aplicada

- `CLIENTE_ENTRANTE_MES = SIM` quando a primeira ocorrência do `ID_FIN` no mês é posterior ao primeiro dia do mês.
- `CELULA_ORIGINAL` preserva a célula real da base de distribuição, calculada pela faixa de atraso.
- `CELULA_VISAO` mostra **Entrantes** para os novos clientes do mês; para os demais, mantém a célula original.

## Importante

A célula **Entrantes** é apenas para visualização e agrupamento do funil/dashboards. O cálculo de H.O. e comissionamento **não usa a célula Entrantes como taxa**.

O comissionamento segue pela regra já ajustada:

```text
H.O. = VALOR_PAGAMENTO real x TAXA_HO da faixa de atraso do título/parcela
```

A faixa/taxa é definida por `DIAS_EM_ATRASO`, calculado a partir de:

```text
DATA_PAGAMENTO_BOLETO - DATA_VENCIMENTO
```

## Visões alteradas

- Dashboard Geral > Resumo por célula
- Funil de Acionamento agrupado por célula
- Régua de Receita > Por célula
- Exportações > Resumo_Celula
- Filtro avançado de célula/visão, incluindo a opção Entrantes
