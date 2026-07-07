from __future__ import annotations

import pandas as pd

from .config import ACIONAMENTOS_REQUIRED, BASE_REQUIRED, PAGAMENTOS_REQUIRED
from .utils import missing_columns


REQUIRED_BY_TYPE = {
    "base": BASE_REQUIRED,
    "pagamentos": PAGAMENTOS_REQUIRED,
    "acionamentos": ACIONAMENTOS_REQUIRED,
    "depara_ocorrencias": ["FINALIZACAO", "TIPO_OCORRENCIA"],
    "depara_atraso": ["DE", "ATE", "FAIXA_ATRASO", "CELULA"],
}


def validate_layout(df: pd.DataFrame, data_type: str) -> tuple[bool, pd.DataFrame]:
    required = REQUIRED_BY_TYPE.get(data_type, [])
    issues = []
    for col in missing_columns(df, required):
        issues.append({"severidade": "CRITICO", "campo": col, "problema": "Coluna obrigatória ausente"})
    if "ID_FIN" in required and "ID_FIN" in df.columns:
        nulls = int(df["ID_FIN"].isna().sum() + (df["ID_FIN"].astype(str).str.strip() == "").sum())
        if nulls:
            issues.append({"severidade": "CRITICO", "campo": "ID_FIN", "problema": f"{nulls} linhas sem ID_FIN"})
    if data_type == "base" and "CPF_CNPJ" in df.columns:
        nulls = int(df["CPF_CNPJ"].isna().sum() + (df["CPF_CNPJ"].astype(str).str.strip() == "").sum())
        if nulls:
            issues.append({"severidade": "ALERTA", "campo": "CPF_CNPJ", "problema": f"{nulls} linhas sem CPF/CNPJ"})
    if data_type == "base" and {"ID_FIN", "DATA_BASE"}.issubset(df.columns):
        dups = int(df.duplicated(["ID_FIN", "DATA_BASE"]).sum())
        if dups:
            issues.append({"severidade": "ALERTA", "campo": "ID_FIN/DATA_BASE", "problema": f"{dups} duplicidades na mesma data"})
    if data_type == "acionamentos" and {"ID_FIN", "DATA_ACIONAMENTO", "FINALIZACAO"}.issubset(df.columns):
        dups = int(df.duplicated(["ID_FIN", "DATA_ACIONAMENTO", "FINALIZACAO"]).sum())
        if dups:
            issues.append({"severidade": "ALERTA", "campo": "ACIONAMENTO", "problema": f"{dups} possíveis duplicidades"})
    critico = any(i["severidade"] == "CRITICO" for i in issues)
    return not critico, pd.DataFrame(issues)
