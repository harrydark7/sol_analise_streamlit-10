from __future__ import annotations

import numpy as np
import pandas as pd

from .transformations import transform_acionamentos, transform_base, transform_pagamentos
from .utils import safe_div

OCC_TYPES = ["TENTATIVA", "ALO", "CPC", "CPCA", "ACORDO"]
# Ordem acumulada do funil operacional.
# Cada registro de uma etapa superior também conta nas etapas anteriores:
# ACORDO => CPCA => CPC => ALO => TENTATIVA.
FUNIL_HIERARCHY = {
    "TENTATIVA": ["TENTATIVA"],
    "ALO": ["TENTATIVA", "ALO"],
    "CPC": ["TENTATIVA", "ALO", "CPC"],
    "CPCA": ["TENTATIVA", "ALO", "CPC", "CPCA"],
    "ACORDO": ["TENTATIVA", "ALO", "CPC", "CPCA", "ACORDO"],
}
SYSTEM_RESPONSAVEIS = {"", "NAN", "NONE", "NULL", "SISTEMA", "AUTOMATICO", "AUTOMATICO SISTEMA", "ROBO", "ROBOT", "BOT"}




def _sum_numeric(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def _mean_numeric(df: pd.DataFrame, col: str) -> float:
    if df is None or df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).mean())


def _valid_operator_mask(df: pd.DataFrame) -> pd.Series:
    """Considera somente registros efetivamente atribuídos a operadores.

    A operação SOL tem acionamentos de sistema/automáticos no histórico. Para o
    funil operacional e para a régua de receita, só entram linhas com
    RESPONSAVEL preenchido e diferente de nomes sistêmicos comuns.
    """
    if df is None or df.empty:
        return pd.Series(dtype=bool)
    if "RESPONSAVEL" not in df.columns:
        return pd.Series(False, index=df.index)
    resp = df["RESPONSAVEL"].fillna("").astype(str).str.strip().str.upper()
    return ~resp.isin(SYSTEM_RESPONSAVEIS)



def normalize_pagamentos_records(pagamentos: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicidades da base de pagamentos sem perder parcelas reais.

    A deduplicação é feita sobre a identidade financeira do pagamento:
    ID_FIN + CCB + parcela + vencimento + data de pagamento + valor pago.
    Isso evita inflar VALOR_PAGAMENTO/H.O. quando o mesmo arquivo é importado
    mais de uma vez ou quando a origem traz linhas repetidas, mas preserva
    múltiplas parcelas pagas pelo mesmo contrato.
    """
    if pagamentos is None or pagamentos.empty:
        return pd.DataFrame(columns=pagamentos.columns if pagamentos is not None else [])
    df = pagamentos.copy()
    if "ID_FIN" in df.columns:
        df["ID_FIN"] = df["ID_FIN"].astype(str)
    for c in ["DATA_VENCIMENTO", "DATA_PAGAMENTO_BOLETO", "DATA_IMPORTACAO", "IMPORTED_AT"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ["VALOR_PAGAMENTO", "HO", "TAXA_HO"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    keys = [c for c in ["ID_FIN", "CCB", "PARCELA", "DATA_VENCIMENTO", "DATA_PAGAMENTO_BOLETO", "VALOR_PAGAMENTO"] if c in df.columns]
    if not keys:
        keys = [c for c in ["ID_FIN", "DATA_PAGAMENTO_BOLETO", "VALOR_PAGAMENTO"] if c in df.columns]
    if keys:
        sort_cols = [c for c in ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_PAGAMENTO_BOLETO"] if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols)
        df = df.drop_duplicates(subset=keys, keep="last")
    return df.reset_index(drop=True)


def _base_context_by_id(base_history: pd.DataFrame, reference_date=None) -> pd.DataFrame:
    """Último contexto conhecido de cada ID_FIN até a data de referência."""
    if base_history is None or base_history.empty or "ID_FIN" not in base_history.columns:
        return pd.DataFrame(columns=["ID_FIN"])
    cols = [c for c in ["ID_FIN", "CPF_CNPJ", "CELULA", "FAIXA_ATRASO", "FUNDO", "CARTEIRA", "DATA_BASE", "DATA_ENTRADA_BASE", "VPL", "VPL_CONVERTIDO", "SALDO_EM_ATRASO", "VALOR_BRUTO"] if c in base_history.columns]
    df = base_history[cols].copy()
    df["ID_FIN"] = df["ID_FIN"].astype(str)
    if "DATA_BASE" in df.columns:
        df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")
        ref = _reference_date(reference_date)
        if ref is not None:
            df = df[df["DATA_BASE"].dt.date <= ref].copy()
        df = df.sort_values(["ID_FIN", "DATA_BASE"]).drop_duplicates(subset=["ID_FIN"], keep="last")
    else:
        df = df.drop_duplicates(subset=["ID_FIN"], keep="last")
    return df.reset_index(drop=True)


def normalize_operator_acionamentos(acionamentos: pd.DataFrame) -> pd.DataFrame:
    """Filtra acionamentos de operadores e remove duplicidades operacionais.

    A chave preferencial usa a data/hora do registro. Quando COD_HISTO existe,
    ele também é preservado na deduplicação; quando não existe, a combinação de
    ID_FIN + data/hora + responsável + finalização evita contar duas vezes o
    mesmo registro importado em duplicidade.
    """
    if acionamentos is None or acionamentos.empty:
        return pd.DataFrame(columns=acionamentos.columns if acionamentos is not None else [])
    df = acionamentos.copy()
    if "ID_FIN" in df.columns:
        df["ID_FIN"] = df["ID_FIN"].astype(str)
    if "DATA_ACIONAMENTO" in df.columns:
        df["DATA_ACIONAMENTO"] = pd.to_datetime(df["DATA_ACIONAMENTO"], errors="coerce")
    if "TIPO_OCORRENCIA" not in df.columns:
        df["TIPO_OCORRENCIA"] = "SEM CLASSIFICACAO"
    df["TIPO_OCORRENCIA"] = df["TIPO_OCORRENCIA"].fillna("SEM CLASSIFICACAO").astype(str).str.upper().str.strip()
    df = df[_valid_operator_mask(df)].copy()
    if df.empty:
        return df
    base_keys = [c for c in ["ID_FIN", "DATA_ACIONAMENTO", "RESPONSAVEL", "FINALIZACAO", "TIPO_OCORRENCIA"] if c in df.columns]
    if "COD_HISTO" in df.columns:
        # Primeiro elimina duplicidade literal do código histórico, quando disponível.
        code_keys = [c for c in ["COD_HISTO", "ID_FIN", "DATA_ACIONAMENTO", "RESPONSAVEL"] if c in df.columns]
        if code_keys:
            df = df.sort_values([c for c in ["DATA_ACIONAMENTO", "COD_HISTO"] if c in df.columns]).drop_duplicates(subset=code_keys, keep="last")
    if base_keys:
        df = df.sort_values([c for c in ["DATA_ACIONAMENTO", "COD_HISTO"] if c in df.columns]).drop_duplicates(subset=base_keys, keep="last")
    return df.reset_index(drop=True)


def add_funil_hierarchy_counts(acionamentos: pd.DataFrame, group_col: str = "ID_FIN") -> pd.DataFrame:
    """Gera contagens acumuladas do funil por grupo, sem duplicar registros.

    Regra operacional:
    - ACORDO soma em ACORDO, CPCA, CPC, ALO e TENTATIVA.
    - CPCA soma em CPCA, CPC, ALO e TENTATIVA.
    - CPC soma em CPC, ALO e TENTATIVA.
    - ALO soma em ALO e TENTATIVA.
    - TENTATIVA soma somente em TENTATIVA.

    A função usa somente acionamentos válidos de operadores e deduplica pela
    data/hora do registro antes da contagem. O cálculo é vetorizado para não
    travar quando o histórico de acionamentos crescer.
    """
    if acionamentos is None or acionamentos.empty or group_col not in acionamentos.columns:
        return pd.DataFrame(columns=[group_col] + OCC_TYPES)

    df = normalize_operator_acionamentos(acionamentos)
    if df.empty or group_col not in df.columns:
        return pd.DataFrame(columns=[group_col] + OCC_TYPES)

    df = df.copy()
    df["TIPO_OCORRENCIA"] = df["TIPO_OCORRENCIA"].fillna("SEM CLASSIFICACAO").astype(str).str.upper().str.strip()

    # Etapas superiores também contam nas etapas anteriores.
    source_by_stage = {
        "TENTATIVA": ["TENTATIVA", "ALO", "CPC", "CPCA", "ACORDO"],
        "ALO": ["ALO", "CPC", "CPCA", "ACORDO"],
        "CPC": ["CPC", "CPCA", "ACORDO"],
        "CPCA": ["CPCA", "ACORDO"],
        "ACORDO": ["ACORDO"],
    }
    for etapa, tipos_origem in source_by_stage.items():
        df[etapa] = df["TIPO_OCORRENCIA"].isin(tipos_origem).astype(int)

    pivot = df.groupby(group_col, dropna=False)[OCC_TYPES].sum().reset_index()
    for t in OCC_TYPES:
        pivot[t] = pd.to_numeric(pivot[t], errors="coerce").fillna(0).astype(int)
    return pivot[[group_col] + OCC_TYPES]


def _as_date_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, pd.Series, pd.Index)):
        vals = value
    else:
        vals = [value]
    out = []
    for v in vals:
        if v in [None, "Todas"]:
            continue
        dt = pd.to_datetime(v, errors="coerce")
        if not pd.isna(dt):
            out.append(dt.date())
    return sorted(set(out))


def _reference_date(value):
    dates = _as_date_list(value)
    if dates:
        return max(dates)
    if value is None or value == "Todas":
        return None
    dt = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(dt) else dt.date()


def filter_by_date(df: pd.DataFrame, col: str, start=None, end=None) -> pd.DataFrame:
    if df is None or df.empty or col not in df.columns:
        return df
    out = df.copy()
    out[col] = pd.to_datetime(out[col], errors="coerce")
    if start is not None:
        out = out[out[col].dt.date >= pd.to_datetime(start).date()]
    if end is not None:
        out = out[out[col].dt.date <= pd.to_datetime(end).date()]
    return out


def latest_base_date(base: pd.DataFrame):
    if base is None or base.empty or "DATA_BASE" not in base.columns:
        return None
    dates = pd.to_datetime(base["DATA_BASE"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date()


def get_base_by_date(base: pd.DataFrame, data_base=None) -> pd.DataFrame:
    return get_base_by_dates(base, [data_base] if data_base not in [None, "Todas"] else None)


def get_base_by_dates(base: pd.DataFrame, datas_base=None) -> pd.DataFrame:
    """Retorna o snapshot vigente da base.

    Mesmo quando o usuário seleciona todas as importações do mês, a base atual
    considerada nos KPIs é sempre a última base enviada dentro da seleção.
    As datas anteriores permanecem disponíveis no histórico para calcular entrada
    real, saída, devolução e reentrada, mas não inflam a carteira atual.
    """
    if base is None or base.empty:
        return pd.DataFrame()
    out = base.copy()
    if "DATA_BASE" not in out.columns:
        return out
    out["DATA_BASE"] = pd.to_datetime(out["DATA_BASE"], errors="coerce")
    dates = _as_date_list(datas_base)
    if dates:
        ref = max(dates)
    else:
        ref = latest_base_date(out)
    if ref is None:
        return out
    out = out[out["DATA_BASE"].dt.date == ref].copy()
    if out.empty:
        return out
    if "ID_FIN" in out.columns:
        out["ID_FIN"] = out["ID_FIN"].astype(str)
        out = out.sort_values(["DATA_BASE", "ID_FIN"]).drop_duplicates(subset=["ID_FIN"], keep="last")
    return out



def _month_period_from_ref(value):
    ref = _reference_date(value)
    if ref is None:
        return None
    return pd.Timestamp(ref).to_period("M")


def _filter_month_until(df: pd.DataFrame, date_col: str, ref_date=None, period=None) -> pd.DataFrame:
    """Filtra um dataframe para o mês da referência e até a data de referência."""
    if df is None or df.empty or date_col not in df.columns:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    out = out.dropna(subset=[date_col])
    if out.empty:
        return out
    if period is None:
        ref = _reference_date(ref_date)
        period = pd.Timestamp(ref).to_period("M") if ref is not None else out[date_col].max().to_period("M")
    out = out[out[date_col].dt.to_period("M") == period].copy()
    ref = _reference_date(ref_date)
    if ref is not None:
        out = out[out[date_col].dt.date <= ref].copy()
    return out

def mark_entrantes_mes(df: pd.DataFrame, base_history: pd.DataFrame, ref_date=None) -> pd.DataFrame:
    """Marca clientes entrantes do mês sem alterar a célula/taxa operacional.

    Entrante = ID_FIN cuja primeira aparição dentro do mês ocorreu após o
    primeiro dia do mês (ex.: a partir de 02/07).

    Campos criados:
    - CELULA_ORIGINAL: célula real calculada pela faixa de atraso;
    - CLIENTE_ENTRANTE_MES: SIM/NAO;
    - DATA_PRIMEIRA_ENTRADA_MES;
    - CELULA_VISAO: "Entrantes" para novos clientes do mês e CELULA_ORIGINAL
      para os demais.

    Importante: CELULA_VISAO serve somente para agrupamento/visão. O cálculo de
    comissionamento continua usando DIAS_EM_ATRASO/FAIXA_ATRASO/TAXA_HO da tabela
    de honorários.
    """
    if df is None or df.empty or "ID_FIN" not in df.columns:
        return pd.DataFrame() if df is None else df
    out = df.copy()
    out["ID_FIN"] = out["ID_FIN"].astype(str)

    if "CELULA_ORIGINAL" not in out.columns:
        if "CELULA" in out.columns:
            out["CELULA_ORIGINAL"] = out["CELULA"]
        else:
            out["CELULA_ORIGINAL"] = "SEM CELULA"

    if base_history is None or base_history.empty or not {"ID_FIN", "DATA_BASE"}.issubset(base_history.columns):
        out["CLIENTE_ENTRANTE_MES"] = "NAO"
        out["DATA_PRIMEIRA_ENTRADA_MES"] = pd.NaT
        out["CELULA_VISAO"] = out["CELULA_ORIGINAL"].fillna("SEM CELULA").astype(str)
        return out

    ref = _reference_date(ref_date) or latest_base_date(base_history)
    if ref is None:
        out["CLIENTE_ENTRANTE_MES"] = "NAO"
        out["DATA_PRIMEIRA_ENTRADA_MES"] = pd.NaT
        out["CELULA_VISAO"] = out["CELULA_ORIGINAL"].fillna("SEM CELULA").astype(str)
        return out

    period = pd.Timestamp(ref).to_period("M")
    month_start = period.start_time.date()
    hist = _filter_month_until(base_history, "DATA_BASE", ref_date=ref, period=period)
    if hist.empty:
        out["CLIENTE_ENTRANTE_MES"] = "NAO"
        out["DATA_PRIMEIRA_ENTRADA_MES"] = pd.NaT
        out["CELULA_VISAO"] = out["CELULA_ORIGINAL"].fillna("SEM CELULA").astype(str)
        return out

    hist = hist.copy()
    hist["ID_FIN"] = hist["ID_FIN"].astype(str)
    first = hist.groupby("ID_FIN", as_index=False).agg(DATA_PRIMEIRA_ENTRADA_MES=("DATA_BASE", "min"))

    out = out.drop(columns=["DATA_PRIMEIRA_ENTRADA_MES", "CLIENTE_ENTRANTE_MES", "CELULA_VISAO"], errors="ignore")
    out = out.merge(first, on="ID_FIN", how="left")
    first_dt = pd.to_datetime(out["DATA_PRIMEIRA_ENTRADA_MES"], errors="coerce")
    is_entrante = first_dt.dt.date > month_start
    out["CLIENTE_ENTRANTE_MES"] = np.where(is_entrante.fillna(False), "SIM", "NAO")
    out["CELULA_VISAO"] = np.where(
        out["CLIENTE_ENTRANTE_MES"].eq("SIM"),
        "Entrantes",
        out["CELULA_ORIGINAL"].fillna("SEM CELULA").astype(str),
    )
    return out



def get_latest_snapshot_for_month(df: pd.DataFrame, date_col: str, ref_date=None) -> pd.DataFrame:
    """Retorna o último snapshot do mês até a data de referência."""
    if df is None or df.empty or date_col not in df.columns:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    month_df = _filter_month_until(df, date_col, ref_date=ref_date)
    if month_df.empty:
        return month_df
    latest = pd.to_datetime(month_df[date_col], errors="coerce").max().date()
    out = month_df[pd.to_datetime(month_df[date_col], errors="coerce").dt.date == latest].copy()
    if "ID_FIN" in out.columns:
        out["ID_FIN"] = out["ID_FIN"].astype(str)
        out = out.sort_values(date_col).drop_duplicates(subset=["ID_FIN"], keep="last")
    return out.reset_index(drop=True)


def base_scope_snapshot(
    base_history: pd.DataFrame,
    ref_date=None,
    scope: str = "Base Ativa do mês",
    base_congelada: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Monta a base que deve ser usada nos indicadores/funil.

    Opções de escopo:
    - Base Ativa do mês: última base diária importada no mês.
    - Base Total do mês: todos os ID_FIN que passaram no mês, com último contexto conhecido.
    - Base Congelada: base de meta do cliente importada em tabela própria.
    - Clientes Entrantes: novos ID_FIN a partir do dia 02 do mês.

    Além do escopo, marca CELULA_VISAO = "Entrantes" para todos os ID_FIN cuja
    primeira entrada no mês ocorreu depois do primeiro dia. A CELULA real
    permanece preservada em CELULA/CELULA_ORIGINAL para cálculo de faixa, taxa
    de H.O. e comissionamento.
    """
    if base_history is None or base_history.empty or "ID_FIN" not in base_history.columns:
        return pd.DataFrame()
    scope_norm = str(scope or "Base Ativa do mês").upper()
    ref = _reference_date(ref_date) or latest_base_date(base_history)
    if ref is None:
        return pd.DataFrame()
    period = pd.Timestamp(ref).to_period("M")
    month_df = _filter_month_until(base_history, "DATA_BASE", ref_date=ref, period=period)
    if month_df.empty:
        return pd.DataFrame()
    month_df = month_df.copy()
    month_df["ID_FIN"] = month_df["ID_FIN"].astype(str)
    month_start = period.start_time.date()
    latest_active = get_latest_snapshot_for_month(base_history, "DATA_BASE", ref_date=ref)
    active_ids = set(latest_active["ID_FIN"].astype(str)) if latest_active is not None and not latest_active.empty and "ID_FIN" in latest_active.columns else set()

    def _finalize(out: pd.DataFrame, escopo: str) -> pd.DataFrame:
        if out is None or out.empty:
            return pd.DataFrame()
        out = out.copy()
        if "ID_FIN" in out.columns:
            out["ID_FIN"] = out["ID_FIN"].astype(str)
        out["ESCOPO_BASE"] = escopo
        out = mark_entrantes_mes(out, base_history, ref)
        return out.reset_index(drop=True)

    if "CONGELADA" in scope_norm:
        frozen = base_congelada.copy() if base_congelada is not None and not base_congelada.empty else pd.DataFrame()
        if not frozen.empty and "ID_FIN" in frozen.columns:
            date_col = "DATA_BASE" if "DATA_BASE" in frozen.columns else "DATA_IMPORTACAO" if "DATA_IMPORTACAO" in frozen.columns else None
            if date_col:
                frozen[date_col] = pd.to_datetime(frozen[date_col], errors="coerce")
                month_frozen = frozen[frozen[date_col].dt.to_period("M") == period].copy()
                if not month_frozen.empty:
                    frozen = month_frozen[month_frozen[date_col] == month_frozen[date_col].max()].copy()
            frozen["ID_FIN"] = frozen["ID_FIN"].astype(str)
            frozen = frozen.drop_duplicates(subset=["ID_FIN"], keep="last")
            frozen["STATUS_CARTEIRA_MES"] = frozen["ID_FIN"].map(lambda x: "ATIVO" if x in active_ids else "FORA_DA_BASE_ATUAL")
            return _finalize(frozen, "BASE_CONGELADA")
        # Fallback útil enquanto a base congelada ainda não for importada: usa o primeiro snapshot do mês.
        first_date = month_df["DATA_BASE"].min().date()
        frozen = month_df[month_df["DATA_BASE"].dt.date == first_date].copy()
        frozen = frozen.drop_duplicates(subset=["ID_FIN"], keep="last")
        frozen["STATUS_CARTEIRA_MES"] = frozen["ID_FIN"].map(lambda x: "ATIVO" if x in active_ids else "FORA_DA_BASE_ATUAL")
        return _finalize(frozen, "BASE_CONGELADA_FALLBACK_PRIMEIRO_DIA")

    if "TOTAL" in scope_norm:
        out = month_df.sort_values(["ID_FIN", "DATA_BASE"]).drop_duplicates(subset=["ID_FIN"], keep="last")
        out["STATUS_CARTEIRA_MES"] = out["ID_FIN"].map(lambda x: "ATIVO" if x in active_ids else "DEVOLVIDO")
        return _finalize(out, "BASE_TOTAL_MES")

    if "ENTRANTE" in scope_norm:
        first = month_df.groupby("ID_FIN", as_index=False).agg(DATA_PRIMEIRA_ENTRADA_MES=("DATA_BASE", "min"))
        entrante_ids = set(first.loc[first["DATA_PRIMEIRA_ENTRADA_MES"].dt.date > month_start, "ID_FIN"].astype(str))
        out = month_df[month_df["ID_FIN"].isin(entrante_ids)].sort_values(["ID_FIN", "DATA_BASE"]).drop_duplicates(subset=["ID_FIN"], keep="last")
        out = out.drop(columns=["DATA_PRIMEIRA_ENTRADA_MES"], errors="ignore").merge(first, on="ID_FIN", how="left")
        out["STATUS_CARTEIRA_MES"] = out["ID_FIN"].map(lambda x: "ATIVO" if x in active_ids else "DEVOLVIDO")
        return _finalize(out, "CLIENTES_ENTRANTES")

    out = latest_active.copy()
    if out is None or out.empty:
        return pd.DataFrame()
    out["STATUS_CARTEIRA_MES"] = "ATIVO"
    return _finalize(out, "BASE_ATIVA_MES")


def base_scope_summary(base_history: pd.DataFrame, ref_date=None, base_congelada: pd.DataFrame | None = None) -> dict:
    """KPIs de composição da carteira no mês: ativa, total, congelada, entrantes e devolvidos."""
    summary = {
        "base_ativa_mes": 0,
        "base_total_mes": 0,
        "cpfs_total_mes": 0,
        "base_congelada": 0,
        "clientes_entrantes": 0,
        "devolvidos_mes": 0,
        "reentradas_mes": 0,
    }
    if base_history is None or base_history.empty or not {"ID_FIN", "DATA_BASE"}.issubset(base_history.columns):
        return summary
    active = base_scope_snapshot(base_history, ref_date, "Base Ativa do mês", base_congelada)
    total = base_scope_snapshot(base_history, ref_date, "Base Total do mês", base_congelada)
    frozen = base_scope_snapshot(base_history, ref_date, "Base Congelada", base_congelada)
    entrantes = base_scope_snapshot(base_history, ref_date, "Clientes Entrantes", base_congelada)
    summary["base_ativa_mes"] = int(active["ID_FIN"].nunique()) if not active.empty and "ID_FIN" in active.columns else 0
    summary["base_total_mes"] = int(total["ID_FIN"].nunique()) if not total.empty and "ID_FIN" in total.columns else 0
    summary["cpfs_total_mes"] = int(total["CPF_CNPJ"].nunique()) if not total.empty and "CPF_CNPJ" in total.columns else 0
    summary["base_congelada"] = int(frozen["ID_FIN"].nunique()) if not frozen.empty and "ID_FIN" in frozen.columns else 0
    summary["clientes_entrantes"] = int(entrantes["ID_FIN"].nunique()) if not entrantes.empty and "ID_FIN" in entrantes.columns else 0
    if not total.empty and "STATUS_CARTEIRA_MES" in total.columns:
        summary["devolvidos_mes"] = int(total["STATUS_CARTEIRA_MES"].astype(str).eq("DEVOLVIDO").sum())
    # Reentradas no mês: saiu em uma data intermediária e voltou depois.
    df = base_history.copy()
    df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")
    ref = _reference_date(ref_date) or latest_base_date(df)
    if ref is not None:
        period = pd.Timestamp(ref).to_period("M")
        df = df[df["DATA_BASE"].dt.to_period("M") == period].copy()
        dates = sorted(df["DATA_BASE"].dropna().dt.date.unique())
        reentries = set()
        for i in range(1, len(dates)):
            cur = set(df[df["DATA_BASE"].dt.date == dates[i]]["ID_FIN"].astype(str))
            prev = set(df[df["DATA_BASE"].dt.date == dates[i - 1]]["ID_FIN"].astype(str))
            before = set(df[df["DATA_BASE"].dt.date < dates[i]]["ID_FIN"].astype(str))
            reentries.update((cur - prev) & before)
        summary["reentradas_mes"] = len(reentries)
    return summary


def latest_payment_import(pagamentos: pd.DataFrame, ref_date=None) -> pd.DataFrame:
    """Usa sempre a última base de pagamento importada no mês como controle oficial."""
    if pagamentos is None or pagamentos.empty:
        return pd.DataFrame(columns=pagamentos.columns if pagamentos is not None else [])
    df = pagamentos.copy()
    date_col = "DATA_IMPORTACAO" if "DATA_IMPORTACAO" in df.columns else "IMPORTED_AT" if "IMPORTED_AT" in df.columns else None
    if date_col is None:
        return normalize_pagamentos_records(df)
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    if df.empty:
        return pd.DataFrame(columns=pagamentos.columns)
    ref = _reference_date(ref_date)
    if ref is not None:
        period = pd.Timestamp(ref).to_period("M")
        month_df = df[df[date_col].dt.to_period("M") == period].copy()
        month_df = month_df[month_df[date_col].dt.date <= ref].copy()
        if not month_df.empty:
            df = month_df
    latest = df[date_col].max().date()
    out = df[df[date_col].dt.date == latest].copy()
    return normalize_pagamentos_records(out)


def pagamentos_disappeared_from_latest(pagamentos: pd.DataFrame, ref_date=None) -> pd.DataFrame:
    """IDs que tinham pagamento em importações anteriores do mês e sumiram da última base importada."""
    if pagamentos is None or pagamentos.empty or "ID_FIN" not in pagamentos.columns:
        return pd.DataFrame()
    df = normalize_pagamentos_records(pagamentos)
    date_col = "DATA_IMPORTACAO" if "DATA_IMPORTACAO" in df.columns else "IMPORTED_AT" if "IMPORTED_AT" in df.columns else None
    if date_col is None:
        return pd.DataFrame()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    if df.empty:
        return pd.DataFrame()
    ref = _reference_date(ref_date)
    if ref is not None:
        period = pd.Timestamp(ref).to_period("M")
        df = df[df[date_col].dt.to_period("M") == period].copy()
    if df.empty:
        return pd.DataFrame()
    latest = df[date_col].max().date()
    latest_ids = set(df[df[date_col].dt.date == latest]["ID_FIN"].astype(str))
    previous = df[df[date_col].dt.date < latest].copy()
    if previous.empty:
        return pd.DataFrame()
    missing = previous[~previous["ID_FIN"].astype(str).isin(latest_ids)].copy()
    if missing.empty:
        return pd.DataFrame()
    for c in ["VALOR_PAGAMENTO", "HO"]:
        if c not in missing.columns:
            missing[c] = 0.0
        missing[c] = pd.to_numeric(missing[c], errors="coerce").fillna(0)
    agg = missing.groupby("ID_FIN", as_index=False).agg(
        VALOR_PAGAMENTO_HISTORICO=("VALOR_PAGAMENTO", "sum"),
        HO_HISTORICO=("HO", "sum"),
        PRIMEIRA_IMPORTACAO=(date_col, "min"),
        ULTIMA_IMPORTACAO_ANTERIOR=(date_col, "max"),
        QTD_LINHAS_HISTORICO=("ID_FIN", "count"),
    )
    agg["STATUS_PAGAMENTO_ULTIMA_BASE"] = "SUMIU_DA_ULTIMA_BASE_PAGAMENTO"
    agg["ULTIMA_BASE_PAGAMENTO"] = latest
    return agg.sort_values("VALOR_PAGAMENTO_HISTORICO", ascending=False)

def get_entry_dates(base_history: pd.DataFrame) -> pd.DataFrame:
    """Retorna a primeira data em que cada ID_FIN entrou na carteira.

    Para o primeiro snapshot disponível no banco, não temos como saber a data
    real de entrada de contratos que já estavam na carteira antes da ferramenta.
    Por isso, esses contratos recebem como entrada o primeiro dia do mês do
    snapshot inaugural. A partir do segundo snapshot, um ID_FIN novo passa a ter
    entrada na data real da primeira aparição.
    """
    if base_history is None or base_history.empty or not {"ID_FIN", "DATA_BASE"}.issubset(base_history.columns):
        return pd.DataFrame(columns=["ID_FIN", "DATA_ENTRADA_BASE"])
    df = base_history[["ID_FIN", "DATA_BASE"]].copy()
    df["ID_FIN"] = df["ID_FIN"].astype(str)
    df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")
    df = df.dropna(subset=["ID_FIN", "DATA_BASE"])
    if df.empty:
        return pd.DataFrame(columns=["ID_FIN", "DATA_ENTRADA_BASE"])
    entry = df.groupby("ID_FIN", as_index=False).agg(DATA_ENTRADA_BASE=("DATA_BASE", "min"))
    first_snapshot = df["DATA_BASE"].min().normalize()
    first_month_start = first_snapshot.to_period("M").start_time.normalize()
    entry["DATA_ENTRADA_BASE"] = pd.to_datetime(entry["DATA_ENTRADA_BASE"], errors="coerce")
    entry.loc[entry["DATA_ENTRADA_BASE"].dt.normalize().eq(first_snapshot), "DATA_ENTRADA_BASE"] = first_month_start
    return entry


def base_month_window(data_base) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    ref_date = _reference_date(data_base)
    if ref_date is None:
        return None, None
    ref = pd.Timestamp(ref_date)
    start = ref.to_period("M").start_time.normalize()
    end = ref.to_period("M").end_time
    return start, end


def apply_base_month_rules(
    base_history: pd.DataFrame,
    base_snapshot: pd.DataFrame,
    pagamentos: pd.DataFrame,
    acionamentos: pd.DataFrame,
    data_base,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Aplica a regra operacional do mês da base.

    A carteira atual continua sendo o último snapshot selecionado. Porém,
    pagamentos e acionamentos elegíveis não ficam limitados apenas aos contratos
    que ainda estão nesse snapshot atual: entram todos os ID_FIN que já passaram
    pela carteira até a data de referência, desde que o evento ocorra dentro do
    mês da base e depois da entrada do ID_FIN. Isso evita perder pagamentos de
    contratos que foram devolvidos/saíram da base antes da última importação.
    """
    if base_history is None or base_history.empty or "ID_FIN" not in base_history.columns:
        return base_snapshot if base_snapshot is not None else pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    base_out = base_snapshot.copy() if base_snapshot is not None else pd.DataFrame()
    if not base_out.empty and "ID_FIN" in base_out.columns:
        base_out["ID_FIN"] = base_out["ID_FIN"].astype(str)

    entry = get_entry_dates(base_history)
    if not base_out.empty:
        if not entry.empty:
            base_out = base_out.drop(columns=["DATA_ENTRADA_BASE"], errors="ignore").merge(entry, on="ID_FIN", how="left")
        else:
            base_out["DATA_ENTRADA_BASE"] = pd.NaT
        if "DATA_BASE" in base_out.columns:
            base_out["DATA_BASE"] = pd.to_datetime(base_out["DATA_BASE"], errors="coerce")
            base_out["DATA_ENTRADA_BASE"] = pd.to_datetime(base_out["DATA_ENTRADA_BASE"], errors="coerce").fillna(base_out["DATA_BASE"])
        # Marca Entrantes para as visões, sem alterar a célula real usada no H.O.
        base_out = mark_entrantes_mes(base_out, base_history, data_base)

    month_start, month_end = base_month_window(data_base)
    ref_date = _reference_date(data_base)

    # Contexto elegível: respeita o escopo de base selecionado pelo usuário
    # (ativa, total do mês, congelada ou entrantes). Assim, a visão "Base Ativa"
    # não puxa pagamentos/acionamentos de contratos que só existem na base total.
    base_context = _base_context_by_id(base_history, reference_date=ref_date)
    if not base_context.empty:
        base_context = mark_entrantes_mes(base_context, base_history, data_base)
    scope_ids = set(base_out["ID_FIN"].astype(str)) if not base_out.empty and "ID_FIN" in base_out.columns else set()
    if scope_ids and not base_context.empty and "ID_FIN" in base_context.columns:
        base_context = base_context[base_context["ID_FIN"].astype(str).isin(scope_ids)].copy()
    if not scope_ids:
        return base_out, pd.DataFrame(columns=pagamentos.columns if pagamentos is not None else []), pd.DataFrame(columns=acionamentos.columns if acionamentos is not None else [])
    if not entry.empty and not base_context.empty:
        base_context = base_context.drop(columns=["DATA_ENTRADA_BASE"], errors="ignore").merge(entry, on="ID_FIN", how="left")
    elif not base_context.empty and "DATA_ENTRADA_BASE" not in base_context.columns:
        base_context["DATA_ENTRADA_BASE"] = pd.NaT
    if "DATA_BASE" in base_context.columns:
        base_context["DATA_BASE"] = pd.to_datetime(base_context["DATA_BASE"], errors="coerce")
        base_context["DATA_ENTRADA_BASE"] = pd.to_datetime(base_context["DATA_ENTRADA_BASE"], errors="coerce").fillna(base_context["DATA_BASE"])

    ids = set(base_context["ID_FIN"].astype(str)) if not base_context.empty and "ID_FIN" in base_context.columns else set()
    entry_map = dict(zip(base_context["ID_FIN"].astype(str), pd.to_datetime(base_context.get("DATA_ENTRADA_BASE", pd.Series(dtype="datetime64[ns]")), errors="coerce"))) if ids else {}

    def _attach_context(out: pd.DataFrame) -> pd.DataFrame:
        if out is None or out.empty or base_context.empty or "ID_FIN" not in out.columns:
            return out
        ctx_cols = [c for c in [
            "ID_FIN", "CPF_CNPJ", "CELULA", "CELULA_ORIGINAL", "CELULA_VISAO", "CLIENTE_ENTRANTE_MES", "DATA_PRIMEIRA_ENTRADA_MES",
            "FAIXA_ATRASO", "FUNDO", "CARTEIRA", "DATA_BASE", "DATA_ENTRADA_BASE",
            "VPL", "VPL_CONVERTIDO", "SALDO_EM_ATRASO", "VALOR_BRUTO"
        ] if c in base_context.columns]
        ctx = base_context[ctx_cols].copy().drop_duplicates(subset=["ID_FIN"], keep="last")
        ctx = ctx.rename(columns={c: f"BASE_{c}" for c in ctx.columns if c != "ID_FIN"})
        return out.merge(ctx, on="ID_FIN", how="left")

    def _filter_events(df: pd.DataFrame, date_col: str, is_payment: bool = False) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=df.columns if df is not None else [])
        if "ID_FIN" not in df.columns or date_col not in df.columns:
            return pd.DataFrame(columns=df.columns)
        out = df.copy()
        if is_payment:
            out = normalize_pagamentos_records(out)
        out["ID_FIN"] = out["ID_FIN"].astype(str)
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
        if ids:
            out = out[out["ID_FIN"].isin(ids)].copy()
        out = out.dropna(subset=[date_col]).copy()
        if month_start is not None and month_end is not None:
            out = out[out[date_col].between(month_start, month_end, inclusive="both")].copy()
        if entry_map:
            out["DATA_ENTRADA_BASE"] = out["ID_FIN"].map(entry_map)
            out = out[out["DATA_ENTRADA_BASE"].isna() | (out[date_col] >= out["DATA_ENTRADA_BASE"])].copy()
        else:
            out["DATA_ENTRADA_BASE"] = pd.NaT
        out["VALIDO_MES_BASE"] = True
        out = _attach_context(out)
        return out

    pagamentos_out = _filter_events(pagamentos, "DATA_PAGAMENTO_BOLETO", is_payment=True)
    acionamentos_out = normalize_operator_acionamentos(_filter_events(acionamentos, "DATA_ACIONAMENTO", is_payment=False))
    return base_out, pagamentos_out, acionamentos_out

def enrich_datasets(
    base: pd.DataFrame,
    pagamentos: pd.DataFrame,
    acionamentos: pd.DataFrame,
    depara_atraso: pd.DataFrame,
    depara_ocorrencias: pd.DataFrame,
    depara_operadores: pd.DataFrame | None = None,
):
    base = transform_base(base, depara_atraso=depara_atraso) if base is not None and not base.empty else pd.DataFrame()
    pagamentos = transform_pagamentos(pagamentos, depara_atraso=depara_atraso) if pagamentos is not None and not pagamentos.empty else pd.DataFrame()
    acionamentos = transform_acionamentos(acionamentos, depara_ocorrencias=depara_ocorrencias, depara_operadores=depara_operadores) if acionamentos is not None and not acionamentos.empty else pd.DataFrame()
    return base, pagamentos, acionamentos


def classificar_regua_receita(base_snapshot: pd.DataFrame, pagamentos: pd.DataFrame, acionamentos: pd.DataFrame) -> pd.DataFrame:
    """Classifica pagamentos conforme a régua de receita/comissionamento.

    Regra financeira reforçada:
    - Saldo VPL é indicador de carteira/meta/produção, NÃO é base de comissão.
    - Receita/H.O. sempre usa VALOR_PAGAMENTO real da base de pagamentos.
    - H.O. = VALOR_PAGAMENTO × TAXA_HO vigente pela faixa/célula de atraso do pagamento.
    - Pagamento comissionável precisa estar elegível pela regra de mês/entrada e possuir
      ACORDO BL antes do pagamento ou acionamento válido antes do pagamento.
    """
    if pagamentos is None or pagamentos.empty:
        return pd.DataFrame(columns=[])

    pay = normalize_pagamentos_records(pagamentos)
    if "ID_FIN" not in pay.columns:
        pay["ID_FIN"] = ""
    pay["ID_FIN"] = pay["ID_FIN"].astype(str)

    # Garante que a base financeira da comissão é sempre o pagamento real.
    if "VALOR_PAGAMENTO" not in pay.columns and "VALOR_PAGO" in pay.columns:
        pay["VALOR_PAGAMENTO"] = pay["VALOR_PAGO"]
    if "VALOR_PAGAMENTO" not in pay.columns:
        pay["VALOR_PAGAMENTO"] = 0.0
    pay["VALOR_PAGAMENTO"] = pd.to_numeric(pay["VALOR_PAGAMENTO"], errors="coerce").fillna(0.0)

    if "TAXA_HO" not in pay.columns:
        pay["TAXA_HO"] = 0.0
    pay["TAXA_HO"] = pd.to_numeric(pay["TAXA_HO"], errors="coerce").fillna(0.0)
    # Proteção: se alguém importar taxa como 18,75 em vez de 0,1875, converte para percentual.
    pay.loc[pay["TAXA_HO"].abs() > 1, "TAXA_HO"] = pay.loc[pay["TAXA_HO"].abs() > 1, "TAXA_HO"] / 100
    pay["HO"] = pay["VALOR_PAGAMENTO"] * pay["TAXA_HO"]
    pay["VALOR_BASE_COMISSAO"] = pay["VALOR_PAGAMENTO"]

    for col in ["DATA_PAGAMENTO_BOLETO", "DATA_VENCIMENTO", "DATA_ENTRADA_BASE"]:
        if col in pay.columns:
            pay[col] = pd.to_datetime(pay[col], errors="coerce")
        else:
            pay[col] = pd.NaT

    # Anexa informações da base apenas como contexto; não usa VPL para receita.
    # Quando apply_base_month_rules já anexou BASE_* usando o histórico completo,
    # preserva esse contexto para pagamentos de contratos devolvidos.
    if base_snapshot is not None and not base_snapshot.empty and "ID_FIN" in base_snapshot.columns:
        missing_base_context = not any(c.startswith("BASE_") for c in pay.columns)
        if missing_base_context:
            base_cols = [c for c in [
                "ID_FIN", "CPF_CNPJ", "CELULA", "CELULA_ORIGINAL", "CELULA_VISAO", "CLIENTE_ENTRANTE_MES", "DATA_PRIMEIRA_ENTRADA_MES",
                "FAIXA_ATRASO", "FUNDO", "CARTEIRA", "DATA_ENTRADA_BASE",
                "VPL", "VPL_CONVERTIDO", "SALDO_EM_ATRASO", "VALOR_BRUTO"
            ] if c in base_snapshot.columns]
            b = base_snapshot[base_cols].copy()
            b["ID_FIN"] = b["ID_FIN"].astype(str)
            b = b.drop_duplicates(subset=["ID_FIN"], keep="last")
            b = b.rename(columns={c: f"BASE_{c}" for c in b.columns if c != "ID_FIN"})
            pay = pay.merge(b, on="ID_FIN", how="left")
        if pay["DATA_ENTRADA_BASE"].isna().all() and "BASE_DATA_ENTRADA_BASE" in pay.columns:
            pay["DATA_ENTRADA_BASE"] = pd.to_datetime(pay["BASE_DATA_ENTRADA_BASE"], errors="coerce")

    # Se a base de pagamento não trouxer célula/faixa, usa a base apenas como fallback visual.
    # CELULA continua sendo a célula real/faixa do pagamento ou da distribuição.
    # CELULA_VISAO é usada somente nas visões para separar os novos clientes como "Entrantes".
    if "CELULA" not in pay.columns and "BASE_CELULA" in pay.columns:
        pay["CELULA"] = pay["BASE_CELULA"]
    if "FAIXA_ATRASO" not in pay.columns and "BASE_FAIXA_ATRASO" in pay.columns:
        pay["FAIXA_ATRASO"] = pay["BASE_FAIXA_ATRASO"]
    if "BASE_CELULA_VISAO" in pay.columns:
        pay["CELULA_VISAO"] = pay["BASE_CELULA_VISAO"].fillna(pay.get("CELULA", "SEM CELULA"))
    elif "CELULA_VISAO" not in pay.columns:
        pay["CELULA_VISAO"] = pay["CELULA"] if "CELULA" in pay.columns else "SEM CELULA"
    if "BASE_CELULA_ORIGINAL" in pay.columns:
        pay["CELULA_ORIGINAL"] = pay["BASE_CELULA_ORIGINAL"]
    elif "CELULA_ORIGINAL" not in pay.columns:
        pay["CELULA_ORIGINAL"] = pay["CELULA"] if "CELULA" in pay.columns else "SEM CELULA"
    if "BASE_CLIENTE_ENTRANTE_MES" in pay.columns:
        pay["CLIENTE_ENTRANTE_MES"] = pay["BASE_CLIENTE_ENTRANTE_MES"].fillna("NAO")
    elif "CLIENTE_ENTRANTE_MES" not in pay.columns:
        pay["CLIENTE_ENTRANTE_MES"] = "NAO"
    if "BASE_VPL_CONVERTIDO" in pay.columns:
        pay["SALDO_VPL_CONTRATO"] = pd.to_numeric(pay["BASE_VPL_CONVERTIDO"], errors="coerce")
    elif "BASE_VPL" in pay.columns:
        pay["SALDO_VPL_CONTRATO"] = pd.to_numeric(pay["BASE_VPL"], errors="coerce")
    elif "VPL_CONVERTIDO" in pay.columns:
        pay["SALDO_VPL_CONTRATO"] = pd.to_numeric(pay["VPL_CONVERTIDO"], errors="coerce")
    elif "VPL" in pay.columns:
        pay["SALDO_VPL_CONTRATO"] = pd.to_numeric(pay["VPL"], errors="coerce")
    else:
        pay["SALDO_VPL_CONTRATO"] = 0.0
    pay["SALDO_VPL_CONTRATO"] = pay["SALDO_VPL_CONTRATO"].fillna(0.0)

    n = len(pay)
    qtd_acionamentos = np.zeros(n, dtype=int)
    qtd_acordos = np.zeros(n, dtype=int)
    qtd_acionamentos_mes = np.zeros(n, dtype=int)
    ultima_data = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    ultima_finalizacao = np.array([""] * n, dtype=object)
    ultimo_responsavel = np.array([""] * n, dtype=object)

    if acionamentos is not None and not acionamentos.empty and {"ID_FIN", "DATA_ACIONAMENTO"}.issubset(acionamentos.columns):
        ac = normalize_operator_acionamentos(acionamentos)
        if ac is not None and not ac.empty:
            ac = ac.copy()
            ac["ID_FIN"] = ac["ID_FIN"].astype(str)
            ac["DATA_ACIONAMENTO"] = pd.to_datetime(ac["DATA_ACIONAMENTO"], errors="coerce")
            ac = ac.dropna(subset=["DATA_ACIONAMENTO"])
            if "TIPO_OCORRENCIA" not in ac.columns:
                ac["TIPO_OCORRENCIA"] = "SEM CLASSIFICACAO"
            ac["TIPO_OCORRENCIA"] = ac["TIPO_OCORRENCIA"].fillna("SEM CLASSIFICACAO").astype(str).str.upper().str.strip()

            # Busca acumulada por ID_FIN usando searchsorted: rápido e respeita data/hora do pagamento.
            pay_dates = pd.to_datetime(pay["DATA_PAGAMENTO_BOLETO"], errors="coerce")
            pay_groups = pay.groupby("ID_FIN", sort=False).groups
            for id_fin, idxs in pay_groups.items():
                ac_id = ac[ac["ID_FIN"] == id_fin].sort_values("DATA_ACIONAMENTO")
                if ac_id.empty:
                    continue
                idxs_arr = np.array(list(idxs), dtype=int)
                # Indicador operacional: o contrato teve qualquer acionamento BL válido no mês.
                # Não depende de ter sido antes do pagamento; é usado para separar Valor Pago com/sem acionamento.
                qtd_acionamentos_mes[idxs_arr] = len(ac_id)
                pg = pay_dates.iloc[idxs_arr].to_numpy(dtype="datetime64[ns]")
                valid_pg = ~np.isnat(pg)
                if not valid_pg.any():
                    continue

                ac_dates = ac_id["DATA_ACIONAMENTO"].to_numpy(dtype="datetime64[ns]")
                counts = np.searchsorted(ac_dates, pg, side="right")
                counts[~valid_pg] = 0
                qtd_acionamentos[idxs_arr] = counts

                last_pos = counts - 1
                has_last = last_pos >= 0
                if has_last.any():
                    locs = last_pos[has_last]
                    target = idxs_arr[has_last]
                    ultima_data[target] = ac_dates[locs]
                    if "FINALIZACAO" in ac_id.columns:
                        ultima_finalizacao[target] = ac_id["FINALIZACAO"].astype(str).to_numpy()[locs]
                    if "RESPONSAVEL" in ac_id.columns:
                        ultimo_responsavel[target] = ac_id["RESPONSAVEL"].astype(str).to_numpy()[locs]

                ac_ag = ac_id[ac_id["TIPO_OCORRENCIA"].eq("ACORDO")]
                if not ac_ag.empty:
                    ag_dates = ac_ag["DATA_ACIONAMENTO"].to_numpy(dtype="datetime64[ns]")
                    ag_counts = np.searchsorted(ag_dates, pg, side="right")
                    ag_counts[~valid_pg] = 0
                    qtd_acordos[idxs_arr] = ag_counts

    status_vencimento = np.where(
        pay["DATA_PAGAMENTO_BOLETO"].isna() | pay["DATA_VENCIMENTO"].isna(),
        "SEM_DATA_PAGAMENTO_OU_VENCIMENTO",
        np.where(
            pay["DATA_PAGAMENTO_BOLETO"].dt.normalize() < pay["DATA_VENCIMENTO"].dt.normalize(),
            "PAGAMENTO_ANTES_DO_VENCIMENTO",
            "OK",
        ),
    )

    comissionavel = (status_vencimento == "OK") & ((qtd_acordos > 0) | (qtd_acionamentos > 0))
    tipo_receita = np.where(
        status_vencimento != "OK",
        status_vencimento,
        np.where(
            qtd_acordos > 0,
            "ACORDO_BL_ANTES_DO_PAGAMENTO",
            np.where(qtd_acionamentos > 0, "INDIRETO_COM_ACIONAMENTO_ANTES_DO_PAGAMENTO", "DIRETO_SEM_ACIONAMENTO_OU_ACORDO"),
        ),
    )

    pay["STATUS_VENCIMENTO"] = status_vencimento
    pay["QTD_ACIONAMENTOS_ANTES_PGTO"] = qtd_acionamentos
    pay["QTD_ACORDOS_ANTES_PGTO"] = qtd_acordos
    pay["QTD_ACIONAMENTOS_MES"] = qtd_acionamentos_mes
    pay["ULTIMA_DATA_ACIONAMENTO_ANTES_PGTO"] = pd.to_datetime(ultima_data, errors="coerce")
    pay["ULTIMA_FINALIZACAO_ANTES_PGTO"] = ultima_finalizacao
    pay["ULTIMO_RESPONSAVEL_ANTES_PGTO"] = ultimo_responsavel
    pay["TIPO_RECEITA"] = tipo_receita
    pay["COMISSIONAVEL"] = np.where(comissionavel, "SIM", "NAO")
    pay["TEM_ACIONAMENTO_ANTES_PGTO"] = np.where(qtd_acionamentos > 0, "SIM", "NAO")
    pay["TEM_ACIONAMENTO_MES"] = np.where(qtd_acionamentos_mes > 0, "SIM", "NAO")
    pay["VALOR_PAGO_COMISSIONAVEL"] = np.where(comissionavel, pay["VALOR_PAGAMENTO"], 0.0)
    pay["HO_COMISSIONAVEL"] = np.where(comissionavel, pay["HO"], 0.0)
    pay["VALOR_PAGO_NAO_COMISSIONAVEL"] = np.where(comissionavel, 0.0, pay["VALOR_PAGAMENTO"])
    pay["HO_NAO_COMISSIONAVEL"] = np.where(comissionavel, 0.0, pay["HO"])
    # Com/Sem acionamento usa qualquer acionamento válido no mês da base.
    # Antes do pagamento continua sendo usado apenas para comissionamento.
    pay["VALOR_PAGO_COM_ACIONAMENTO"] = np.where(qtd_acionamentos_mes > 0, pay["VALOR_PAGAMENTO"], 0.0)
    pay["VALOR_PAGO_SEM_ACIONAMENTO"] = np.where(qtd_acionamentos_mes > 0, 0.0, pay["VALOR_PAGAMENTO"])
    pay["SALDO_VPL_PAGO_COM_ACIONAMENTO"] = np.where(qtd_acionamentos_mes > 0, pay["SALDO_VPL_CONTRATO"], 0.0)
    pay["SALDO_VPL_PAGO_SEM_ACIONAMENTO"] = np.where(qtd_acionamentos_mes > 0, 0.0, pay["SALDO_VPL_CONTRATO"])
    pay["OBS_COMISSIONAMENTO"] = "Comissão calculada sobre VALOR_PAGAMENTO real, nunca sobre Saldo VPL. Saldo VPL pago é indicador de produção/carteira."

    return pay

def _unique_vpl_sum(df: pd.DataFrame, mask=None) -> float:
    """Soma Saldo VPL uma única vez por ID_FIN."""
    if df is None or df.empty or "ID_FIN" not in df.columns or "SALDO_VPL_CONTRATO" not in df.columns:
        return 0.0
    tmp = df.copy()
    if mask is not None:
        tmp = tmp[mask].copy()
    if tmp.empty:
        return 0.0
    tmp["SALDO_VPL_CONTRATO"] = pd.to_numeric(tmp["SALDO_VPL_CONTRATO"], errors="coerce").fillna(0)
    return float(tmp.sort_values("SALDO_VPL_CONTRATO").drop_duplicates(subset=["ID_FIN"], keep="last")["SALDO_VPL_CONTRATO"].sum())



def pagamentos_contrato_summary(pagamentos_regua: pd.DataFrame, group_col: str | None = None) -> pd.DataFrame:
    """Consolida pagamentos por ID_FIN antes de calcular H.O.

    Fluxo solicitado para a carteira SOL:
    1. Remove duplicidades da base de pagamento.
    2. Calcula H.O. por título/parcela usando DIAS_EM_ATRASO = DATA_PAGAMENTO - DATA_VENCIMENTO
       e a taxa vigente da tabela de honorários.
    3. Consolida VALOR_PAGAMENTO e H.O. por ID_FIN/contrato para exibição.

    Assim, a receita nunca usa Saldo VPL como base de comissão e também não aplica
    uma taxa única/maxima sobre todo o contrato quando há parcelas em faixas diferentes.
    """
    if pagamentos_regua is None or pagamentos_regua.empty:
        return pd.DataFrame()
    df = normalize_pagamentos_records(pagamentos_regua)
    if df.empty or "ID_FIN" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["ID_FIN"] = df["ID_FIN"].astype(str)
    numeric_cols = [
        "VALOR_PAGAMENTO", "TAXA_HO", "VALOR_PAGO_COMISSIONAVEL", "VALOR_PAGO_NAO_COMISSIONAVEL",
        "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO", "SALDO_VPL_CONTRATO",
        "QTD_ACIONAMENTOS_MES", "HO", "HO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL"
    ]
    for c in numeric_cols:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    if "TEM_ACIONAMENTO_ANTES_PGTO" not in df.columns:
        df["TEM_ACIONAMENTO_ANTES_PGTO"] = "NAO"
    if "TEM_ACIONAMENTO_MES" not in df.columns:
        df["TEM_ACIONAMENTO_MES"] = "NAO"
    if "COMISSIONAVEL" not in df.columns:
        df["COMISSIONAVEL"] = "NAO"

    agg_dict = {
        "VALOR_PAGAMENTO": ("VALOR_PAGAMENTO", "sum"),
        "TAXA_HO_CONTRATO": ("TAXA_HO", "max"),
        "HO_CONTRATO": ("HO", "sum"),
        "VALOR_PAGO_COMISSIONAVEL": ("VALOR_PAGO_COMISSIONAVEL", "sum"),
        "HO_COMISSIONAVEL": ("HO_COMISSIONAVEL", "sum"),
        "VALOR_PAGO_NAO_COMISSIONAVEL": ("VALOR_PAGO_NAO_COMISSIONAVEL", "sum"),
        "HO_NAO_COMISSIONAVEL": ("HO_NAO_COMISSIONAVEL", "sum"),
        "VALOR_PAGO_COM_ACIONAMENTO": ("VALOR_PAGO_COM_ACIONAMENTO", "sum"),
        "VALOR_PAGO_SEM_ACIONAMENTO": ("VALOR_PAGO_SEM_ACIONAMENTO", "sum"),
        "SALDO_VPL_CONTRATO": ("SALDO_VPL_CONTRATO", "max"),
        "QTD_LINHAS_PAGAMENTO": ("ID_FIN", "count"),
        "TEM_ACIONAMENTO_ANTES_PGTO": ("TEM_ACIONAMENTO_ANTES_PGTO", lambda s: "SIM" if s.astype(str).eq("SIM").any() else "NAO"),
        "TEM_ACIONAMENTO_MES": ("TEM_ACIONAMENTO_MES", lambda s: "SIM" if s.astype(str).eq("SIM").any() else "NAO"),
        "QTD_ACIONAMENTOS_MES": ("QTD_ACIONAMENTOS_MES", "max"),
        "COMISSIONAVEL": ("COMISSIONAVEL", lambda s: "SIM" if s.astype(str).eq("SIM").any() else "NAO"),
    }
    for c in [
        "CELULA", "CELULA_ORIGINAL", "CELULA_VISAO", "CLIENTE_ENTRANTE_MES",
        "FAIXA_ATRASO", "BASE_CELULA", "BASE_CELULA_ORIGINAL", "BASE_CELULA_VISAO", "BASE_CLIENTE_ENTRANTE_MES",
        "BASE_FAIXA_ATRASO", "TIPO_RECEITA", "FUNDO", "CARTEIRA"
    ]:
        if c in df.columns:
            agg_dict[c] = (c, lambda s: s.dropna().astype(str).iloc[0] if len(s.dropna()) else "")
    if group_col and group_col in df.columns and group_col not in agg_dict:
        agg_dict[group_col] = (group_col, lambda s: s.dropna().astype(str).iloc[0] if len(s.dropna()) else "")

    out = df.groupby("ID_FIN", as_index=False).agg(**agg_dict)
    # H.O. já vem calculado por título/parcela e é apenas somado no contrato.
    # Não recalcular usando taxa máxima do ID_FIN.
    out["SALDO_VPL_PAGO_COM_ACIONAMENTO"] = np.where(out["TEM_ACIONAMENTO_MES"].astype(str).eq("SIM"), out["SALDO_VPL_CONTRATO"], 0.0)
    out["SALDO_VPL_PAGO_SEM_ACIONAMENTO"] = np.where(out["TEM_ACIONAMENTO_MES"].astype(str).eq("SIM"), 0.0, out["SALDO_VPL_CONTRATO"])
    out["OBS_CALCULO_HO"] = "H.O. por título: VALOR_PAGAMENTO x TAXA_HO da faixa do título; depois soma por ID_FIN. Saldo VPL não é base de comissão."
    return out

def receita_kpis(pagamentos_regua: pd.DataFrame) -> dict:
    if pagamentos_regua is None or pagamentos_regua.empty:
        return {
            "valor_pago_total": 0.0, "ho_total": 0.0,
            "valor_pago_comissionavel": 0.0, "ho_comissionavel": 0.0,
            "valor_pago_nao_comissionavel": 0.0, "ho_nao_comissionavel": 0.0,
            "pagamentos_total": 0, "contratos_pagos": 0, "pagamentos_comissionaveis": 0,
            "saldo_vpl_pago_total": 0.0, "saldo_vpl_pago_com_acionamento": 0.0,
            "saldo_vpl_pago_sem_acionamento": 0.0, "valor_pago_com_acionamento": 0.0,
            "valor_pago_sem_acionamento": 0.0,
        }
    df = normalize_pagamentos_records(pagamentos_regua)
    contratos = pagamentos_contrato_summary(df)
    if contratos.empty:
        return {
            "valor_pago_total": 0.0, "ho_total": 0.0,
            "valor_pago_comissionavel": 0.0, "ho_comissionavel": 0.0,
            "valor_pago_nao_comissionavel": 0.0, "ho_nao_comissionavel": 0.0,
            "pagamentos_total": 0, "contratos_pagos": 0, "pagamentos_comissionaveis": 0,
            "saldo_vpl_pago_total": 0.0, "saldo_vpl_pago_com_acionamento": 0.0,
            "saldo_vpl_pago_sem_acionamento": 0.0, "valor_pago_com_acionamento": 0.0,
            "valor_pago_sem_acionamento": 0.0,
        }
    return {
        "valor_pago_total": _sum_numeric(contratos, "VALOR_PAGAMENTO"),
        "ho_total": _sum_numeric(contratos, "HO_CONTRATO"),
        "valor_pago_comissionavel": _sum_numeric(contratos, "VALOR_PAGO_COMISSIONAVEL"),
        "ho_comissionavel": _sum_numeric(contratos, "HO_COMISSIONAVEL"),
        "valor_pago_nao_comissionavel": _sum_numeric(contratos, "VALOR_PAGO_NAO_COMISSIONAVEL"),
        "ho_nao_comissionavel": _sum_numeric(contratos, "HO_NAO_COMISSIONAVEL"),
        "valor_pago_com_acionamento": _sum_numeric(contratos, "VALOR_PAGO_COM_ACIONAMENTO"),
        "valor_pago_sem_acionamento": _sum_numeric(contratos, "VALOR_PAGO_SEM_ACIONAMENTO"),
        "saldo_vpl_pago_total": _sum_numeric(contratos, "SALDO_VPL_CONTRATO"),
        "saldo_vpl_pago_com_acionamento": _sum_numeric(contratos, "SALDO_VPL_PAGO_COM_ACIONAMENTO"),
        "saldo_vpl_pago_sem_acionamento": _sum_numeric(contratos, "SALDO_VPL_PAGO_SEM_ACIONAMENTO"),
        "pagamentos_total": len(df),
        "contratos_pagos": int(contratos["ID_FIN"].nunique()) if "ID_FIN" in contratos.columns else 0,
        "pagamentos_comissionaveis": int((contratos.get("COMISSIONAVEL", pd.Series(dtype=str)).astype(str) == "SIM").sum()),
    }


def receita_by(pagamentos_regua: pd.DataFrame, group_col: str = "TIPO_RECEITA") -> pd.DataFrame:
    if pagamentos_regua is None or pagamentos_regua.empty or not group_col:
        return pd.DataFrame()
    df = normalize_pagamentos_records(pagamentos_regua)
    if group_col not in df.columns:
        return pd.DataFrame()
    contratos = pagamentos_contrato_summary(df, group_col=group_col)
    if contratos.empty or group_col not in contratos.columns:
        return pd.DataFrame()
    for c in [
        "VALOR_PAGAMENTO", "HO_CONTRATO", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL",
        "VALOR_PAGO_NAO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL",
        "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO", "SALDO_VPL_CONTRATO",
        "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO"
    ]:
        if c not in contratos.columns:
            contratos[c] = 0.0
        contratos[c] = pd.to_numeric(contratos[c], errors="coerce").fillna(0)
    base = contratos.groupby(group_col, dropna=False).agg(
        QTD_PAGAMENTOS=("QTD_LINHAS_PAGAMENTO", "sum"),
        CONTRATOS=("ID_FIN", "nunique"),
        VALOR_PAGO=("VALOR_PAGAMENTO", "sum"),
        HO=("HO_CONTRATO", "sum"),
        VALOR_PAGO_COMISSIONAVEL=("VALOR_PAGO_COMISSIONAVEL", "sum"),
        HO_COMISSIONAVEL=("HO_COMISSIONAVEL", "sum"),
        VALOR_PAGO_NAO_COMISSIONAVEL=("VALOR_PAGO_NAO_COMISSIONAVEL", "sum"),
        HO_NAO_COMISSIONAVEL=("HO_NAO_COMISSIONAVEL", "sum"),
        VALOR_PAGO_COM_ACIONAMENTO=("VALOR_PAGO_COM_ACIONAMENTO", "sum"),
        VALOR_PAGO_SEM_ACIONAMENTO=("VALOR_PAGO_SEM_ACIONAMENTO", "sum"),
        SALDO_VPL_TOTAL_PAGO=("SALDO_VPL_CONTRATO", "sum"),
        SALDO_VPL_PAGO_COM_ACIONAMENTO=("SALDO_VPL_PAGO_COM_ACIONAMENTO", "sum"),
        SALDO_VPL_PAGO_SEM_ACIONAMENTO=("SALDO_VPL_PAGO_SEM_ACIONAMENTO", "sum"),
    ).reset_index()
    return base.sort_values("VALOR_PAGO", ascending=False)


def enrich_base_with_activity(base: pd.DataFrame, pagamentos: pd.DataFrame, acionamentos: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty:
        return pd.DataFrame() if base is None else base
    out = base.copy()
    if "ID_FIN" not in out.columns:
        return out
    out["ID_FIN"] = out["ID_FIN"].astype(str)

    # Remove colunas herdadas do Excel com textos como SIM/NAO para evitar soma/concatenação.
    activity_cols = OCC_TYPES + [
        "SEM_TENTATIVA", "VALOR_PAGO", "HO_PAGAMENTOS", "VALOR_PAGO_COMISSIONAVEL",
        "HO_COMISSIONAVEL", "VALOR_PAGO_NAO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL",
        "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO", "SALDO_VPL_CONTRATO",
        "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO"
    ]
    out = out.drop(columns=[c for c in activity_cols if c in out.columns], errors="ignore")

    if pagamentos is not None and not pagamentos.empty and "ID_FIN" in pagamentos.columns and "VALOR_PAGAMENTO" in pagamentos.columns:
        pay_agg = pagamentos_contrato_summary(pagamentos)
        if not pay_agg.empty:
            pay_agg = pay_agg.rename(columns={
                "VALOR_PAGAMENTO": "VALOR_PAGO",
                "HO_CONTRATO": "HO_PAGAMENTOS",
                "SALDO_VPL_CONTRATO": "SALDO_VPL_TOTAL_PAGO",
            })
            keep_cols = [c for c in [
                "ID_FIN", "VALOR_PAGO", "HO_PAGAMENTOS", "VALOR_PAGO_COMISSIONAVEL",
                "HO_COMISSIONAVEL", "VALOR_PAGO_NAO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL",
                "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO",
                "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO",
                "TAXA_HO_CONTRATO", "QTD_LINHAS_PAGAMENTO"
            ] if c in pay_agg.columns]
            out = out.merge(pay_agg[keep_cols], on="ID_FIN", how="left")


    for c in [
        "VALOR_PAGO", "HO_PAGAMENTOS", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL",
        "VALOR_PAGO_NAO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL",
        "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO",
        "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO"
    ]:
        if c not in out.columns:
            out[c] = 0.0
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)

    if acionamentos is not None and not acionamentos.empty and {"ID_FIN", "TIPO_OCORRENCIA"}.issubset(acionamentos.columns):
        pivot = add_funil_hierarchy_counts(acionamentos, group_col="ID_FIN")
        if not pivot.empty:
            out = out.merge(pivot, on="ID_FIN", how="left")

    for t in OCC_TYPES:
        if t not in out.columns:
            out[t] = 0
        out[t] = pd.to_numeric(out[t], errors="coerce").fillna(0).astype(int)
    out["SEM_TENTATIVA"] = out["TENTATIVA"].eq(0).astype(int)
    return out


def geral_kpis(base_enriched: pd.DataFrame, pagamentos: pd.DataFrame, acionamentos: pd.DataFrame) -> dict:
    if base_enriched is None or base_enriched.empty:
        return {
            "contratos": 0, "cpfs": 0, "vpl": 0, "valor_bruto": 0, "saldo_atraso": 0,
            "valor_pago": 0, "ho": 0, "valor_pago_comissionavel": 0, "ho_comissionavel": 0,
            "valor_pago_nao_comissionavel": 0, "ho_nao_comissionavel": 0,
            "saldo_vpl_pago_total": 0, "saldo_vpl_pago_com_acionamento": 0, "saldo_vpl_pago_sem_acionamento": 0,
            "valor_pago_com_acionamento": 0, "valor_pago_sem_acionamento": 0,
            "tentativa": 0, "alo": 0, "cpc": 0, "cpca": 0, "acordo": 0,
            "perc_cpc": 0, "conversao": 0, "sem_tentativa": 0
        }
    k = {}
    k["contratos"] = base_enriched["ID_FIN"].nunique() if "ID_FIN" in base_enriched.columns else len(base_enriched)
    k["cpfs"] = base_enriched["CPF_CNPJ"].nunique() if "CPF_CNPJ" in base_enriched.columns else 0
    k["vpl"] = _sum_numeric(base_enriched, "VPL_CONVERTIDO") or _sum_numeric(base_enriched, "VPL")
    k["valor_bruto"] = _sum_numeric(base_enriched, "VALOR_BRUTO")
    k["saldo_atraso"] = _sum_numeric(base_enriched, "SALDO_EM_ATRASO")
    rk_tmp = receita_kpis(pagamentos)
    k["valor_pago"] = rk_tmp.get("valor_pago_total", 0.0)
    k["ho"] = rk_tmp.get("ho_total", 0.0)
    k["valor_pago_comissionavel"] = rk_tmp.get("valor_pago_comissionavel", 0.0)
    k["ho_comissionavel"] = rk_tmp.get("ho_comissionavel", 0.0)
    k["valor_pago_nao_comissionavel"] = rk_tmp.get("valor_pago_nao_comissionavel", 0.0)
    k["ho_nao_comissionavel"] = rk_tmp.get("ho_nao_comissionavel", 0.0)
    k["saldo_vpl_pago_total"] = rk_tmp.get("saldo_vpl_pago_total", 0.0)
    k["saldo_vpl_pago_com_acionamento"] = rk_tmp.get("saldo_vpl_pago_com_acionamento", 0.0)
    k["saldo_vpl_pago_sem_acionamento"] = rk_tmp.get("saldo_vpl_pago_sem_acionamento", 0.0)
    k["valor_pago_com_acionamento"] = rk_tmp.get("valor_pago_com_acionamento", 0.0)
    k["valor_pago_sem_acionamento"] = rk_tmp.get("valor_pago_sem_acionamento", 0.0)
    for t in OCC_TYPES:
        k[t.lower()] = _sum_numeric(base_enriched, t)
    k["perc_cpc"] = safe_div(k["cpc"], k["alo"])
    k["conversao"] = safe_div(k["acordo"], k["cpca"])
    k["sem_tentativa"] = _sum_numeric(base_enriched, "SEM_TENTATIVA")
    return k


def funil_by(base_enriched: pd.DataFrame, group_col: str = "CELULA") -> pd.DataFrame:
    if base_enriched is None or base_enriched.empty or group_col not in base_enriched.columns:
        return pd.DataFrame()
    df = base_enriched.copy()
    value_col = "VPL_CONVERTIDO" if "VPL_CONVERTIDO" in df.columns else "VPL"
    for c in [value_col, "VALOR_PAGO", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL", "SEM_TENTATIVA", "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO", "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO"] + OCC_TYPES:
        if c not in df.columns:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    agg = df.groupby(group_col, dropna=False).agg(
        UNIQUE=("ID_FIN", "nunique") if "ID_FIN" in df.columns else (group_col, "count"),
        CPFS=("CPF_CNPJ", "nunique") if "CPF_CNPJ" in df.columns else (group_col, "count"),
        SALDO_VPL=(value_col, "sum"),
        VALOR_PAGO=("VALOR_PAGO", "sum"),
        VALOR_PAGO_COMISSIONAVEL=("VALOR_PAGO_COMISSIONAVEL", "sum"),
        HO_COMISSIONAVEL=("HO_COMISSIONAVEL", "sum"),
        VALOR_PAGO_COM_ACIONAMENTO=("VALOR_PAGO_COM_ACIONAMENTO", "sum"),
        VALOR_PAGO_SEM_ACIONAMENTO=("VALOR_PAGO_SEM_ACIONAMENTO", "sum"),
        SALDO_VPL_TOTAL_PAGO=("SALDO_VPL_TOTAL_PAGO", "sum"),
        SALDO_VPL_PAGO_COM_ACIONAMENTO=("SALDO_VPL_PAGO_COM_ACIONAMENTO", "sum"),
        SALDO_VPL_PAGO_SEM_ACIONAMENTO=("SALDO_VPL_PAGO_SEM_ACIONAMENTO", "sum"),
        SEM_TENTATIVA=("SEM_TENTATIVA", "sum"),
        TENTATIVA=("TENTATIVA", "sum"),
        ALO=("ALO", "sum"),
        CPC=("CPC", "sum"),
        CPCA=("CPCA", "sum"),
        ACORDO=("ACORDO", "sum"),
    ).reset_index()
    agg["% CPC"] = agg.apply(lambda r: safe_div(r["CPC"], r["ALO"]), axis=1)
    agg["CONVERSAO"] = agg.apply(lambda r: safe_div(r["ACORDO"], r["CPCA"]), axis=1)
    return agg.sort_values("SALDO_VPL", ascending=False)


def operadores(acionamentos: pd.DataFrame) -> pd.DataFrame:
    if acionamentos is None or acionamentos.empty or "RESPONSAVEL" not in acionamentos.columns:
        return pd.DataFrame()
    df = normalize_operator_acionamentos(acionamentos)
    if df.empty:
        return pd.DataFrame()

    pivot = add_funil_hierarchy_counts(df, group_col="RESPONSAVEL")
    if pivot.empty:
        pivot = pd.DataFrame({"RESPONSAVEL": sorted(df["RESPONSAVEL"].dropna().unique())})
        for t in OCC_TYPES:
            pivot[t] = 0

    total = df.groupby("RESPONSAVEL").size().rename("TOTAL_REGISTROS_UNICOS").reset_index()
    contratos = df.groupby("RESPONSAVEL")["ID_FIN"].nunique().rename("CONTRATOS_ACIONADOS").reset_index() if "ID_FIN" in df.columns else pd.DataFrame()
    pivot = pivot.merge(total, on="RESPONSAVEL", how="left")
    if not contratos.empty:
        pivot = pivot.merge(contratos, on="RESPONSAVEL", how="left")
    else:
        pivot["CONTRATOS_ACIONADOS"] = 0
    for t in OCC_TYPES:
        if t not in pivot.columns:
            pivot[t] = 0
        pivot[t] = pd.to_numeric(pivot[t], errors="coerce").fillna(0).astype(int)
    pivot["TOTAL_ACIONAMENTOS"] = pd.to_numeric(pivot["TOTAL_REGISTROS_UNICOS"], errors="coerce").fillna(0).astype(int)
    pivot["% CPC"] = pivot.apply(lambda r: safe_div(r["CPC"], r["ALO"]), axis=1)
    pivot["CONVERSAO"] = pivot.apply(lambda r: safe_div(r["ACORDO"], r["CPCA"]), axis=1)
    return pivot.sort_values("TOTAL_REGISTROS_UNICOS", ascending=False)

def novas_entradas(base: pd.DataFrame, data_base) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Retorna entradas, saídas, mantidos e alterações de faixa versus data anterior.

    Status tratados:
    - NOVA_ENTRADA: ID_FIN nunca havia aparecido antes.
    - REENTRADA: ID_FIN não estava na base anterior, mas já havia aparecido antes.
    - DEVOLVIDO: saiu da base e não aparece novamente em datas futuras do histórico.
    - SAIDA_TEMPORARIA: saiu na comparação, mas reaparece depois.
    """
    if base is None or base.empty or "DATA_BASE" not in base.columns or "ID_FIN" not in base.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    df = base.copy()
    df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")
    df = df.dropna(subset=["DATA_BASE"])
    df["ID_FIN"] = df["ID_FIN"].astype(str)
    current_date = pd.to_datetime(data_base).date()
    current = df[df["DATA_BASE"].dt.date == current_date].copy()
    previous_dates = sorted(d for d in df["DATA_BASE"].dropna().dt.date.unique() if d < current_date)
    future_dates = sorted(d for d in df["DATA_BASE"].dropna().dt.date.unique() if d > current_date)
    if not previous_dates:
        current["STATUS_MOVIMENTO"] = "NOVA_ENTRADA"
        return current.copy(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    prev_date = previous_dates[-1]
    prev = df[df["DATA_BASE"].dt.date == prev_date].copy()
    prev_ids = set(prev["ID_FIN"].astype(str))
    current_ids = set(current["ID_FIN"].astype(str))
    historical_before_ids = set(df[df["DATA_BASE"].dt.date < current_date]["ID_FIN"].astype(str))
    future_ids = set(df[df["DATA_BASE"].dt.date.isin(future_dates)]["ID_FIN"].astype(str)) if future_dates else set()

    new = current[~current["ID_FIN"].astype(str).isin(prev_ids)].copy()
    new["STATUS_MOVIMENTO"] = new["ID_FIN"].astype(str).map(lambda x: "REENTRADA" if x in historical_before_ids else "NOVA_ENTRADA")

    removed = prev[~prev["ID_FIN"].astype(str).isin(current_ids)].copy()
    removed["STATUS_MOVIMENTO"] = removed["ID_FIN"].astype(str).map(lambda x: "SAIDA_TEMPORARIA" if x in future_ids else "DEVOLVIDO")

    kept = current[current["ID_FIN"].astype(str).isin(prev_ids)].copy()
    kept["STATUS_MOVIMENTO"] = "MANTIDO"

    changes = pd.DataFrame()
    if "FAIXA_ATRASO" in current.columns and "FAIXA_ATRASO" in prev.columns:
        cmp = current[["ID_FIN", "FAIXA_ATRASO"]].merge(prev[["ID_FIN", "FAIXA_ATRASO"]], on="ID_FIN", how="inner", suffixes=("_ATUAL", "_ANTERIOR"))
        changes = cmp[cmp["FAIXA_ATRASO_ATUAL"] != cmp["FAIXA_ATRASO_ANTERIOR"]].copy()
        if not changes.empty:
            changes["STATUS_MOVIMENTO"] = "MUDANCA_DE_FAIXA"
    return new, removed, kept, changes

def base_evolution_summary(base: pd.DataFrame, selected_month=None) -> pd.DataFrame:
    """Resumo dia a dia: carteira anterior, atual, entradas, saídas, reentradas, devoluções e VPL."""
    if base is None or base.empty or not {"DATA_BASE", "ID_FIN"}.issubset(base.columns):
        return pd.DataFrame()
    df = base.copy()
    df["DATA_BASE"] = pd.to_datetime(df["DATA_BASE"], errors="coerce")
    df = df.dropna(subset=["DATA_BASE"])
    if df.empty:
        return pd.DataFrame()
    df["ID_FIN"] = df["ID_FIN"].astype(str)
    value_col = "VPL_CONVERTIDO" if "VPL_CONVERTIDO" in df.columns else "VPL" if "VPL" in df.columns else None
    if value_col:
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce").fillna(0)
    else:
        value_col = "__ZERO__"
        df[value_col] = 0.0

    all_dates = sorted(df["DATA_BASE"].dt.date.unique())
    if selected_month is not None:
        period = pd.Period(str(selected_month), freq="M") if not isinstance(selected_month, pd.Period) else selected_month
        display_dates = [d for d in all_dates if pd.Timestamp(d).to_period("M") == period]
    else:
        display_dates = all_dates

    records = []
    for current_date in display_dates:
        previous_dates = [d for d in all_dates if d < current_date]
        future_dates = [d for d in all_dates if d > current_date]
        prev_date = previous_dates[-1] if previous_dates else None
        cur = df[df["DATA_BASE"].dt.date == current_date].copy()
        prev = df[df["DATA_BASE"].dt.date == prev_date].copy() if prev_date else pd.DataFrame(columns=df.columns)

        cur_ids = set(cur["ID_FIN"])
        prev_ids = set(prev["ID_FIN"]) if not prev.empty else set()
        before_ids = set(df[df["DATA_BASE"].dt.date < current_date]["ID_FIN"].astype(str))
        future_ids = set(df[df["DATA_BASE"].dt.date.isin(future_dates)]["ID_FIN"].astype(str)) if future_dates else set()

        new_ids = cur_ids - prev_ids
        reentry_ids = new_ids & before_ids
        first_entry_ids = new_ids - before_ids
        removed_ids = prev_ids - cur_ids
        devolvidos_ids = removed_ids - future_ids
        saida_temp_ids = removed_ids & future_ids
        kept_ids = cur_ids & prev_ids

        records.append({
            "DATA_BASE": current_date,
            "DATA_BASE_ANTERIOR": prev_date,
            "BASE_ATUAL": "SIM" if current_date == all_dates[-1] else "NAO",
            "QTD_DIA_ANTERIOR": len(prev_ids),
            "QTD_DIA_ATUAL": len(cur_ids),
            "ENTRADAS_ID_FIN": len(new_ids),
            "NOVAS_ENTRADAS_ID_FIN": len(first_entry_ids),
            "REENTRADAS_ID_FIN": len(reentry_ids),
            "SAIDAS_ID_FIN": len(removed_ids),
            "DEVOLVIDOS_ID_FIN": len(devolvidos_ids),
            "SAIDAS_TEMPORARIAS_ID_FIN": len(saida_temp_ids),
            "MANTIDOS_ID_FIN": len(kept_ids),
            "DELTA_ID_FIN": len(cur_ids) - len(prev_ids),
            "CPFS_DIA_ATUAL": cur["CPF_CNPJ"].nunique() if "CPF_CNPJ" in cur.columns else len(cur_ids),
            "VPL_DIA_ANTERIOR": float(prev[value_col].sum()) if not prev.empty else 0.0,
            "VPL_DIA_ATUAL": float(cur[value_col].sum()),
            "VPL_ENTRADAS": float(cur[cur["ID_FIN"].isin(new_ids)][value_col].sum()) if new_ids else 0.0,
            "VPL_REENTRADAS": float(cur[cur["ID_FIN"].isin(reentry_ids)][value_col].sum()) if reentry_ids else 0.0,
            "VPL_SAIDAS": float(prev[prev["ID_FIN"].isin(removed_ids)][value_col].sum()) if removed_ids and not prev.empty else 0.0,
            "VPL_DEVOLVIDOS": float(prev[prev["ID_FIN"].isin(devolvidos_ids)][value_col].sum()) if devolvidos_ids and not prev.empty else 0.0,
            "DELTA_VPL": float(cur[value_col].sum()) - (float(prev[value_col].sum()) if not prev.empty else 0.0),
        })
    return pd.DataFrame(records)

def cpf_unico(base_enriched: pd.DataFrame, acionamentos: pd.DataFrame) -> pd.DataFrame:
    if base_enriched is None or base_enriched.empty or "CPF_CNPJ" not in base_enriched.columns:
        return pd.DataFrame()
    df = base_enriched.copy()
    value_col = "VPL_CONVERTIDO" if "VPL_CONVERTIDO" in df.columns else "VPL"
    df["__ZERO__"] = 0.0
    for c in [value_col, "VALOR_BRUTO", "SALDO_EM_ATRASO", "DIAS_ATRASO", "VALOR_PAGO", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL"] + OCC_TYPES:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    agg = df.groupby("CPF_CNPJ", dropna=False).agg(
        QTD_CONTRATOS=("ID_FIN", "nunique"),
        IDS_FIN=("ID_FIN", lambda s: ", ".join(sorted(set(map(str, s))))),
        VPL_TOTAL=(value_col, "sum"),
        VALOR_BRUTO_TOTAL=("VALOR_BRUTO", "sum") if "VALOR_BRUTO" in df.columns else (value_col, "sum"),
        SALDO_ATRASO_TOTAL=("SALDO_EM_ATRASO", "sum") if "SALDO_EM_ATRASO" in df.columns else (value_col, "sum"),
        MAIOR_ATRASO=("DIAS_ATRASO", "max") if "DIAS_ATRASO" in df.columns else ("ID_FIN", "count"),
        MENOR_ATRASO=("DIAS_ATRASO", "min") if "DIAS_ATRASO" in df.columns else ("ID_FIN", "count"),
        VALOR_PAGO=("VALOR_PAGO", "sum") if "VALOR_PAGO" in df.columns else ("__ZERO__", "sum"),
        VALOR_PAGO_COMISSIONAVEL=("VALOR_PAGO_COMISSIONAVEL", "sum") if "VALOR_PAGO_COMISSIONAVEL" in df.columns else ("__ZERO__", "sum"),
        HO_COMISSIONAVEL=("HO_COMISSIONAVEL", "sum") if "HO_COMISSIONAVEL" in df.columns else ("__ZERO__", "sum"),
        TENTATIVA=("TENTATIVA", "sum") if "TENTATIVA" in df.columns else ("ID_FIN", "count"),
        ALO=("ALO", "sum") if "ALO" in df.columns else ("ID_FIN", "count"),
        CPC=("CPC", "sum") if "CPC" in df.columns else ("ID_FIN", "count"),
        CPCA=("CPCA", "sum") if "CPCA" in df.columns else ("ID_FIN", "count"),
        ACORDO=("ACORDO", "sum") if "ACORDO" in df.columns else ("ID_FIN", "count"),
    ).reset_index()
    agg["POSSUI_PAGAMENTO"] = agg["VALOR_PAGO"].gt(0).map({True: "SIM", False: "NAO"})
    agg["POSSUI_ACIONAMENTO"] = agg["TENTATIVA"].gt(0).map({True: "SIM", False: "NAO"})

    if acionamentos is not None and not acionamentos.empty and {"ID_FIN", "DATA_ACIONAMENTO"}.issubset(acionamentos.columns):
        last = acionamentos.copy()
        last["DATA_ACIONAMENTO"] = pd.to_datetime(last["DATA_ACIONAMENTO"], errors="coerce")
        ids = df[["CPF_CNPJ", "ID_FIN"]].drop_duplicates()
        last = last.merge(ids, on="ID_FIN", how="left")
        last = last.sort_values("DATA_ACIONAMENTO").groupby("CPF_CNPJ", as_index=False).tail(1)
        cols = [c for c in ["CPF_CNPJ", "DATA_ACIONAMENTO", "FINALIZACAO", "RESPONSAVEL"] if c in last.columns]
        last = last[cols].rename(columns={"DATA_ACIONAMENTO": "ULTIMA_DATA_ACIONAMENTO", "FINALIZACAO": "ULTIMA_FINALIZACAO", "RESPONSAVEL": "ULTIMO_RESPONSAVEL"})
        agg = agg.merge(last, on="CPF_CNPJ", how="left")

    sort_cols = [c for c in [value_col, "SALDO_EM_ATRASO", "DIAS_ATRASO"] if c in df.columns]
    if sort_cols:
        main = df.sort_values(sort_cols, ascending=[False] * len(sort_cols)).groupby("CPF_CNPJ", as_index=False).head(1)
        keep_cols = [c for c in ["CPF_CNPJ", "ID_FIN", "FAIXA_ATRASO", "CELULA", "FUNDO", "CARTEIRA"] if c in main.columns]
        main = main[keep_cols].rename(columns={"ID_FIN": "ID_FIN_PRINCIPAL", "FAIXA_ATRASO": "FAIXA_PRINCIPAL", "CELULA": "CELULA_PRINCIPAL", "FUNDO": "FUNDO_PRINCIPAL", "CARTEIRA": "CARTEIRA_PRINCIPAL"})
        agg = agg.merge(main, on="CPF_CNPJ", how="left")
    return agg.sort_values("VPL_TOTAL", ascending=False)
