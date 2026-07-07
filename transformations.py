from __future__ import annotations

import pandas as pd

from .utils import as_id, normalize_text, only_digits, parse_date_series, parse_money


TEXT_COLS = ["FUNDO", "TIPO_CLIENTE", "CARTEIRA", "ETAPA_ATUAL", "CARENCIA", "STATUS_COBRANCA", "SITU_COB", "FINALIZACAO", "RESPONSAVEL", "CANAL_ACIONAMENTO", "TIPO_ACIONAMENTO", "MOTIVO_ATRASO"]
MONEY_COLS = ["VALOR_BRUTO", "VPL", "SALDO_EM_ATRASO", "VPL_CONVERTIDO", "VALOR_PAGAMENTO", "HO", "META"]
DATE_COLS = ["DATA_BASE", "DATA_IMPORTACAO", "DATA_VENCIMENTO", "DATA_PAGAMENTO_BOLETO", "DATA_ACIONAMENTO", "DATA_AGENDADA"]


def clean_common(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["ID_FIN", "ID", "CCB", "COD_HISTO", "COD_PRODUT", "PARCELA"]:
        if col in df.columns:
            df[col] = df[col].map(as_id)
    if "ID" in df.columns and "ID_FIN" not in df.columns:
        df = df.rename(columns={"ID": "ID_FIN"})
    if "CPF_CNPJ" in df.columns:
        df["CPF_CNPJ"] = df["CPF_CNPJ"].map(only_digits)
    for col in TEXT_COLS:
        if col in df.columns:
            df[col] = df[col].map(normalize_text)
    for col in MONEY_COLS:
        if col in df.columns:
            df[col] = df[col].map(parse_money)
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = parse_date_series(df[col])
    for col in ["DIAS_ATRASO", "DIAS_EM_ATRASO", "DE", "ATE"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "TAXA_HO" in df.columns:
        # Aceita taxa como 0,1875, 18,75 ou "18,75%" e padroniza para decimal.
        raw_taxa = df["TAXA_HO"].copy()
        pct_mask = raw_taxa.astype(str).str.contains("%", regex=False, na=False)
        numeric_taxa = raw_taxa.astype(str).str.replace("%", "", regex=False).map(parse_money)
        numeric_taxa = pd.to_numeric(numeric_taxa, errors="coerce").fillna(0)
        numeric_taxa.loc[pct_mask] = numeric_taxa.loc[pct_mask] / 100
        numeric_taxa.loc[numeric_taxa.abs() > 1] = numeric_taxa.loc[numeric_taxa.abs() > 1] / 100
        df["TAXA_HO"] = numeric_taxa
    return df


def normalize_depara_atraso(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_common(df)
    keep = [c for c in ["DE", "ATE", "FAIXA_ATRASO", "CELULA", "TAXA_HO"] if c in df.columns]
    df = df[keep].dropna(subset=["DE", "ATE"], how="any") if {"DE", "ATE"}.issubset(df.columns) else df[keep]
    if "TAXA_HO" not in df.columns:
        df["TAXA_HO"] = 0.0
    return df.drop_duplicates().reset_index(drop=True)


def _truthy_stage(value) -> bool:
    return normalize_text(value) in {"SIM", "S", "YES", "Y", "TRUE", "1"}


def normalize_depara_ocorrencias(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza DePara de ocorrências em dois formatos.

    Formato antigo: FINALIZACAO + TIPO_OCORRENCIA.
    Formato novo enviado pelo usuário: MOTIVO_ATRASO + colunas TENTATIVA/ALO/CPC/CPCA/ACORDO.
    Neste formato, MOTIVO_ATRASO representa o texto da ocorrência/finalização.
    Quando houver duplicidade no DePara, prevalece a etapa mais alta do funil.
    """
    df = clean_common(df)
    priority = {"TENTATIVA": 1, "ALO": 2, "CPC": 3, "CPCA": 4, "ACORDO": 5}

    # Formato novo: MOTIVO_ATRASO com flags/hierarquia.
    if "MOTIVO_ATRASO" in df.columns and any(c in df.columns for c in ["TENTATIVA", "ALO", "CPC", "CPCA", "ACORDO"]):
        rows = []
        for _, row in df.iterrows():
            finalizacao = normalize_text(row.get("MOTIVO_ATRASO", ""))
            if not finalizacao:
                continue
            stage = normalize_text(row.get("TENTATIVA", ""))
            if stage not in priority:
                stage = "TENTATIVA"
            for etapa in ["ALO", "CPC", "CPCA", "ACORDO"]:
                if etapa in df.columns and _truthy_stage(row.get(etapa, "")) and priority[etapa] > priority[stage]:
                    stage = etapa
            rows.append({"FINALIZACAO": finalizacao, "TIPO_OCORRENCIA": stage, "PRIORIDADE": priority.get(stage, 1)})
        if not rows:
            return pd.DataFrame(columns=["FINALIZACAO", "TIPO_OCORRENCIA"])
        out = pd.DataFrame(rows)
        out = out.sort_values("PRIORIDADE").drop_duplicates(subset=["FINALIZACAO"], keep="last")
        return out[["FINALIZACAO", "TIPO_OCORRENCIA"]].reset_index(drop=True)

    # Formato antigo.
    keep = [c for c in ["FINALIZACAO", "TIPO_OCORRENCIA"] if c in df.columns]
    df = df[keep].dropna(how="all") if keep else pd.DataFrame(columns=["FINALIZACAO", "TIPO_OCORRENCIA"])
    if "FINALIZACAO" not in df.columns:
        df["FINALIZACAO"] = ""
    if "TIPO_OCORRENCIA" not in df.columns:
        df["TIPO_OCORRENCIA"] = "TENTATIVA"
    df["FINALIZACAO"] = df["FINALIZACAO"].map(normalize_text)
    df["TIPO_OCORRENCIA"] = df["TIPO_OCORRENCIA"].map(normalize_text)
    df.loc[~df["TIPO_OCORRENCIA"].isin(priority), "TIPO_OCORRENCIA"] = "TENTATIVA"
    df["PRIORIDADE"] = df["TIPO_OCORRENCIA"].map(priority).fillna(1)
    df = df[df["FINALIZACAO"] != ""].sort_values("PRIORIDADE").drop_duplicates(subset=["FINALIZACAO"], keep="last")
    return df[["FINALIZACAO", "TIPO_OCORRENCIA"]].reset_index(drop=True)


def normalize_depara_operadores(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_common(df)
    if "OPERADOR" in df.columns and "RESPONSAVEL" not in df.columns:
        df = df.rename(columns={"OPERADOR": "RESPONSAVEL"})
    keep = [c for c in ["RESPONSAVEL", "ORIGEM_ACIONAMENTO", "SUPERVISORA"] if c in df.columns]
    out = df[keep].dropna(how="all") if keep else pd.DataFrame(columns=["RESPONSAVEL", "ORIGEM_ACIONAMENTO", "SUPERVISORA"])
    if "RESPONSAVEL" not in out.columns:
        out["RESPONSAVEL"] = ""
    for c in ["RESPONSAVEL", "ORIGEM_ACIONAMENTO", "SUPERVISORA"]:
        if c not in out.columns:
            out[c] = ""
        out[c] = out[c].map(normalize_text)
    out = out[out["RESPONSAVEL"] != ""].drop_duplicates(subset=["RESPONSAVEL"], keep="last")
    return out.reset_index(drop=True)

def classify_by_atraso(values: pd.Series, depara_atraso: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=values.index, columns=["FAIXA_ATRASO", "CELULA", "TAXA_HO"])
    out["FAIXA_ATRASO"] = "SEM FAIXA"
    out["CELULA"] = "SEM CELULA"
    out["TAXA_HO"] = 0.0
    if depara_atraso.empty or "DE" not in depara_atraso.columns or "ATE" not in depara_atraso.columns:
        return out
    dp = normalize_depara_atraso(depara_atraso)
    for _, row in dp.iterrows():
        mask = values.ge(row["DE"]) & values.le(row["ATE"])
        out.loc[mask, "FAIXA_ATRASO"] = row.get("FAIXA_ATRASO", "SEM FAIXA")
        out.loc[mask, "CELULA"] = row.get("CELULA", "SEM CELULA")
        out.loc[mask, "TAXA_HO"] = row.get("TAXA_HO", 0.0) or 0.0
    return out


def transform_base(df: pd.DataFrame, data_base=None, depara_atraso: pd.DataFrame | None = None) -> pd.DataFrame:
    df = clean_common(df)
    if data_base is not None:
        df["DATA_BASE"] = pd.to_datetime(data_base)
    elif "DATA_BASE" not in df.columns:
        df["DATA_BASE"] = pd.Timestamp.today().normalize()
    if "VPL_CONVERTIDO" not in df.columns and "VPL" in df.columns:
        df["VPL_CONVERTIDO"] = df["VPL"]
    if "DIAS_ATRASO" in df.columns and depara_atraso is not None and not depara_atraso.empty:
        classif = classify_by_atraso(df["DIAS_ATRASO"], depara_atraso)
        df["FAIXA_ATRASO"] = classif["FAIXA_ATRASO"]
        df["CELULA"] = classif["CELULA"]
        if "TAXA_HO" not in df.columns:
            df["TAXA_HO"] = classif["TAXA_HO"]
    return df


def transform_pagamentos(df: pd.DataFrame, depara_atraso: pd.DataFrame | None = None) -> pd.DataFrame:
    df = clean_common(df)

    # Base financeira da receita: sempre o valor efetivamente pago.
    # Saldo VPL é usado em carteira/meta/produção, nunca para comissão.
    if "VALOR_PAGAMENTO" not in df.columns and "VALOR_PAGO" in df.columns:
        df["VALOR_PAGAMENTO"] = df["VALOR_PAGO"]
    if "VALOR_PAGAMENTO" not in df.columns:
        df["VALOR_PAGAMENTO"] = 0.0
    df["VALOR_PAGAMENTO"] = pd.to_numeric(df["VALOR_PAGAMENTO"], errors="coerce").fillna(0.0)

    if "DATA_PAGAMENTO_BOLETO" in df.columns and "DATA_VENCIMENTO" in df.columns:
        df["DIAS_EM_ATRASO"] = (df["DATA_PAGAMENTO_BOLETO"] - df["DATA_VENCIMENTO"]).dt.days
    elif "DIAS_ATRASO" in df.columns and "DIAS_EM_ATRASO" not in df.columns:
        df["DIAS_EM_ATRASO"] = df["DIAS_ATRASO"]

    if depara_atraso is not None and not depara_atraso.empty and "DIAS_EM_ATRASO" in df.columns:
        classif = classify_by_atraso(df["DIAS_EM_ATRASO"], depara_atraso)
        df["FAIXA_ATRASO"] = classif["FAIXA_ATRASO"]
        df["CELULA"] = classif["CELULA"]
        df["TAXA_HO"] = pd.to_numeric(classif["TAXA_HO"], errors="coerce").fillna(0.0)

    if "TAXA_HO" not in df.columns:
        df["TAXA_HO"] = 0.0
    df["TAXA_HO"] = pd.to_numeric(df["TAXA_HO"], errors="coerce").fillna(0.0)
    df.loc[df["TAXA_HO"].abs() > 1, "TAXA_HO"] = df.loc[df["TAXA_HO"].abs() > 1, "TAXA_HO"] / 100

    # Recalcula sempre o H.O. para evitar reaproveitar coluna antiga do Excel ou da base.
    df["HO"] = df["VALOR_PAGAMENTO"] * df["TAXA_HO"]
    df["VALOR_BASE_COMISSAO"] = df["VALOR_PAGAMENTO"]
    return df


def transform_acionamentos(
    df: pd.DataFrame,
    depara_ocorrencias: pd.DataFrame | None = None,
    depara_operadores: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = clean_common(df)

    # Classificação do funil usando o DePara atualizado.
    if depara_ocorrencias is not None and not depara_ocorrencias.empty:
        dp = normalize_depara_ocorrencias(depara_ocorrencias)
        mapper = dict(zip(dp["FINALIZACAO"], dp["TIPO_OCORRENCIA"])) if not dp.empty else {}
        tipo = pd.Series("", index=df.index, dtype="object")
        if "FINALIZACAO" in df.columns:
            tipo = df["FINALIZACAO"].map(normalize_text).map(mapper).fillna("")
        # Fallback: alguns arquivos usam MOTIVO_ATRASO como descrição da ocorrência.
        if "MOTIVO_ATRASO" in df.columns:
            motivo_tipo = df["MOTIVO_ATRASO"].map(normalize_text).map(mapper).fillna("")
            tipo = tipo.mask(tipo.eq(""), motivo_tipo)
        df["TIPO_OCORRENCIA"] = tipo
    elif "TIPO_OCORRENCIA" not in df.columns:
        df["TIPO_OCORRENCIA"] = ""

    df["TIPO_OCORRENCIA"] = df["TIPO_OCORRENCIA"].map(normalize_text)
    # Regra solicitada: linha sem classificação/motivo em branco conta como tentativa.
    df.loc[df["TIPO_OCORRENCIA"].eq("") | df["TIPO_OCORRENCIA"].eq("SEM CLASSIFICACAO"), "TIPO_OCORRENCIA"] = "TENTATIVA"

    # Enriquecimento com origem do operador/equipe.
    if depara_operadores is not None and not depara_operadores.empty and "RESPONSAVEL" in df.columns:
        ops = normalize_depara_operadores(depara_operadores)
        if not ops.empty:
            df = df.drop(columns=["ORIGEM_ACIONAMENTO", "SUPERVISORA"], errors="ignore")
            df = df.merge(ops, on="RESPONSAVEL", how="left")
            if "ORIGEM_ACIONAMENTO" not in df.columns:
                df["ORIGEM_ACIONAMENTO"] = "NAO MAPEADO"
            if "SUPERVISORA" not in df.columns:
                df["SUPERVISORA"] = ""
            df["ORIGEM_ACIONAMENTO"] = df["ORIGEM_ACIONAMENTO"].fillna("NAO MAPEADO")
            df["SUPERVISORA"] = df["SUPERVISORA"].fillna("")
    return df

def transform_metas(df: pd.DataFrame) -> pd.DataFrame:
    df = clean_common(df)
    if "CELULA" not in df.columns and "FAIXA_ATRASO" in df.columns:
        df["CELULA"] = df["FAIXA_ATRASO"]
    return df
