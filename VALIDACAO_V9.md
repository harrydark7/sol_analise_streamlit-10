# Validação V9 — SOL / Solfácil

Versão ajustada com base nas bases enviadas de 01/07 a 07/07 e nos DeParas atualizados.

## Principais validações realizadas

- Última base oficial de pagamentos do mês: **Pagamentos 0707.xlsx**.
- Valor pago total deduplicado da última base: **R$ 1.039.393,57**.
- Valor pago de ID_FIN com acionamento BL válido dentro do mês: **R$ 357.483,03**.
- Valor pago de ID_FIN sem acionamento BL válido dentro do mês: **R$ 681.910,54**.
- Total de registros únicos de acionamento considerados em Finalização 0707: **3.687**.
- Funil acumulado após DePara atualizado:
  - Tentativa: **3.687**
  - Alô: **2.267**
  - CPC: **652**
  - CPCA: **519**
  - Acordo: **383**

## Regras ajustadas

1. Linhas sem classificação ou com finalização/motivo não mapeado passam a contar como **TENTATIVA**.
2. O funil é acumulado: ACORDO conta em CPCA, CPC, ALO e TENTATIVA.
3. A separação de pagamento com/sem acionamento usa qualquer acionamento BL válido no mês.
4. A comissão/H.O. continua exigindo acordo ou acionamento antes do pagamento quando aplicável.
5. H.O. é calculado por título/parcela: `VALOR_PAGAMENTO x TAXA_HO` da faixa do título, depois somado por ID_FIN.
6. Saldo VPL não é usado como base de comissão.
7. A tela possui filtro por `CANAL_ACIONAMENTO` e origem do operador.
8. Operadores foram enriquecidos com origem BL/INTERNA e supervisora.
