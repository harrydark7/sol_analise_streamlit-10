from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import normalize_column_name


ALIASES = {
    # Base
    "ID": "ID_FIN",
    "ID_FIN": "ID_FIN",
    "FUNDO": "FUNDO",
    "TIPO_DO_CLIENTE": "TIPO_CLIENTE",
    "TIPO_CLIENTE": "TIPO_CLIENTE",
    "CCB": "CCB",
    "DIAS_DE_ATRASO": "DIAS_ATRASO",
    "DIAS_ATRASO": "DIAS_ATRASO",
    "CARTEIRA": "CARTEIRA",
    "VALOR_BRUTO": "VALOR_BRUTO",
    "VPL": "VPL",
    "SALDO_EM_ATRASO": "SALDO_EM_ATRASO",
    "PARCELAS_EM_ATRASO": "PARCELAS_EM_ATRASO",
    "PARCELAS_PAGAS": "PARCELAS_PAGAS",
    "ETAPA_ATUAL": "ETAPA_ATUAL",
    "CARENCIA": "CARENCIA",
    "STATUS_COBRANCA": "STATUS_COBRANCA",
    "STATUS_COBRANCA_": "STATUS_COBRANCA",
    "NOVO": "NOVO",
    "COD_PRODUT": "COD_PRODUT",
    "COD_PRODUTO": "COD_PRODUT",
    "CPF_CNPJ": "CPF_CNPJ",
    "CPF": "CPF_CNPJ",
    "CNPJ": "CPF_CNPJ",
    "SITU_COB": "SITU_COB",
    "ACORDO": "ACORDO",
    "ACORDO2": "ACORDO",
    "VPL_CONVERTIDO": "VPL_CONVERTIDO",
    "DATA_DA_BASE": "DATA_BASE",
    "DATA_BASE": "DATA_BASE",
    # Pagamentos
    "PARCELA": "PARCELA",
    "DATA_VENCIMENTO": "DATA_VENCIMENTO",
    "DATAVENCIMENTO": "DATA_VENCIMENTO",
    "DATA_PAGAMENTO_BOLETO": "DATA_PAGAMENTO_BOLETO",
    "DATAPAGAMENTOBOLETO": "DATA_PAGAMENTO_BOLETO",
    "VALOR_PAGAMENTO": "VALOR_PAGAMENTO",
    "VALORPAGAMENTO": "VALOR_PAGAMENTO",
    "DIAS_EM_ATRASO": "DIAS_EM_ATRASO",
    "FAIXA_DE_ATRASO": "FAIXA_ATRASO",
    "FAIXA_ATRASO": "FAIXA_ATRASO",
    "TAXA_DE_HO": "TAXA_HO",
    "TAXA_DE_H_O": "TAXA_HO",
    "TAXA_HO": "TAXA_HO",
    "H_O": "HO",
    "HO": "HO",
    # Acionamentos
    "COD_HISTO": "COD_HISTO",
    "FINALIZACAO": "FINALIZACAO",
    "DATA_ACIONAMENTO": "DATA_ACIONAMENTO",
    "DATA_AGENDADA": "DATA_AGENDADA",
    "MOTIVO_ATRASO": "MOTIVO_ATRASO",
    "MOTIVO_DE_ATRASO": "MOTIVO_ATRASO",
    "CARTEIRA_DISTRIBUIDA": "CARTEIRA_DISTRIBUIDA",
    "RESPONSAVEL": "RESPONSAVEL",
    "TELEFONE_ACIONADO": "TELEFONE_ACIONADO",
    "TIPO_ACIONAMENTO": "TIPO_ACIONAMENTO",
    "CANAL_ACIONAMENTO": "CANAL_ACIONAMENTO",
    "COMENTARIO_ACIONAMENTO": "COMENTARIO_ACIONAMENTO",
    "TIPO_DE_OCORRENCIA": "TIPO_OCORRENCIA",
    "TIPO_OCORRENCIA": "TIPO_OCORRENCIA",
    # DePara
    "OCORRENCIA": "FINALIZACAO",
    "CATEGORIA": "TIPO_OCORRENCIA",
    "DE": "DE",
    "ATE": "ATE",
    "EQUIPE": "FAIXA_ATRASO",
    "EMPRESA": "CELULA",
    "CELULA": "CELULA",
    "OPERADOR": "OPERADOR",
    "OPERADORES": "OPERADOR",
    "ORIGEM_DO_ACIONAMENTO": "ORIGEM_ACIONAMENTO",
    "ORIGEM_ACIONAMENTO": "ORIGEM_ACIONAMENTO",
    "SUPERVISORA": "SUPERVISORA",
    "META": "META",
}


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    new_cols = []
    for col in df.columns:
        norm = normalize_column_name(col)
        new_cols.append(ALIASES.get(norm, norm))
    df.columns = new_cols
    # Remove colunas totalmente vazias ou colunas "UNNAMED"
    df = df.loc[:, [c for c in df.columns if not c.startswith("UNNAMED") and c != ""]]
    return df


def _looks_like_bad_header(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    cols = [normalize_column_name(c) for c in df.columns]
    useful = {"ID_FIN", "ID", "DE", "ATE", "FINALIZACAO", "MOTIVO_ATRASO", "RESPONSAVEL", "DATA_ACIONAMENTO", "VALORPAGAMENTO", "VALOR_PAGAMENTO"}
    if any(c in useful or ALIASES.get(c) in useful for c in cols):
        return False
    unnamed = sum(str(c).upper().startswith("UNNAMED") for c in cols)
    return unnamed >= max(1, len(cols) // 2)


def _read_excel_smart(file_obj: Any, sheet_name: str | int | None = None) -> pd.DataFrame:
    """Lê Excel detectando cabeçalho deslocado.

    Alguns DeParas enviados vêm com linhas em branco acima do cabeçalho.
    Este leitor procura uma linha contendo chaves como DE/ATE ou MOTIVO_ATRASO
    e usa essa linha como cabeçalho automaticamente.
    """
    sheet = sheet_name or 0
    df = pd.read_excel(file_obj, sheet_name=sheet)
    if not _looks_like_bad_header(df):
        return standardize_columns(df)

    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
    except Exception:
        pass
    raw = pd.read_excel(file_obj, sheet_name=sheet, header=None)
    header_idx = None
    targets = {"ID", "ID_FIN", "DE", "ATE", "MOTIVO_ATRASO", "FINALIZACAO", "RESPONSAVEL", "VALORPAGAMENTO", "DATAACIONAMENTO"}
    for idx in range(min(25, len(raw))):
        vals = {normalize_column_name(v) for v in raw.iloc[idx].tolist() if not pd.isna(v)}
        vals_alias = {ALIASES.get(v, v) for v in vals}
        score = len(vals_alias.intersection(targets)) + len(vals.intersection(targets))
        if score >= 2 or {"DE", "ATE"}.issubset(vals_alias):
            header_idx = idx
            break
    if header_idx is None:
        return standardize_columns(df)
    header = raw.iloc[header_idx].tolist()
    data = raw.iloc[header_idx + 1 :].copy()
    data.columns = header
    data = data.dropna(how="all")
    return standardize_columns(data)


def read_table(file_obj: Any, sheet_name: str | int | None = None) -> pd.DataFrame:
    """Lê CSV/XLSX de upload Streamlit ou caminho local."""
    name = getattr(file_obj, "name", None) or str(file_obj)
    suffix = Path(name).suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xls"]:
        return _read_excel_smart(file_obj, sheet_name=sheet_name or 0)
    if suffix == ".csv":
        try:
            return standardize_columns(pd.read_csv(file_obj, sep=None, engine="python", encoding="utf-8-sig"))
        except UnicodeDecodeError:
            return standardize_columns(pd.read_csv(file_obj, sep=None, engine="python", encoding="latin1"))
    raise ValueError(f"Formato não suportado: {suffix}")


def list_excel_sheets(file_obj: Any) -> list[str]:
    name = getattr(file_obj, "name", None) or str(file_obj)
    suffix = Path(name).suffix.lower()
    if suffix not in [".xlsx", ".xlsm", ".xls"]:
        return []
    pos = None
    try:
        pos = file_obj.tell()
    except Exception:
        pass
    xls = pd.ExcelFile(file_obj)
    if pos is not None:
        try:
            file_obj.seek(pos)
        except Exception:
            pass
    return xls.sheet_names


def read_named_sheets_from_workbook(file_obj: Any) -> dict[str, pd.DataFrame]:
    """Lê um workbook completo e tenta localizar Base, Pagamentos, Acionamento e DePara."""
    sheets = list_excel_sheets(file_obj)
    result: dict[str, pd.DataFrame] = {}
    targets = {
        "base": ["Base", "BASE"],
        "pagamentos": ["Pagamentos", "PAGAMENTOS"],
        "acionamentos": ["Acionamento", "ACIONAMENTO"],
        "depara": ["DePara", "DEPARA", "De Para"],
    }
    for key, names in targets.items():
        match = next((s for s in sheets if s in names or normalize_column_name(s) in [normalize_column_name(n) for n in names]), None)
        if match:
            result[key] = read_table(file_obj, sheet_name=match)
            try:
                file_obj.seek(0)
            except Exception:
                pass
    return result
