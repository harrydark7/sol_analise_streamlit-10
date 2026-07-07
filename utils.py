from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Iterable

import pandas as pd


def normalize_text(value) -> str:
    """Padroniza texto para comparação: sem acento, maiúsculo, espaços simples."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text)
    return text.upper().strip()


def normalize_column_name(value) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^A-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def only_digits(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\D+", "", str(value))


def as_id(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if re.match(r"^\d+\.0$", text):
        text = text[:-2]
    return text


def parse_money(value) -> float:
    if pd.isna(value) or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace(" ", "")
    # Se tiver vírgula decimal no padrão BR, transforma para ponto.
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_date_series(series: pd.Series) -> pd.Series:
    """Converte datas preservando ISO yyyy-mm-dd e aceitando formatos BR dd/mm/yyyy."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    text = series.astype("string").str.strip()
    iso_mask = text.str.match(r"^\d{4}-\d{1,2}-\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$").fillna(False)
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    if iso_mask.any():
        out.loc[iso_mask] = pd.to_datetime(series.loc[iso_mask], errors="coerce", dayfirst=False)
    if (~iso_mask).any():
        out.loc[~iso_mask] = pd.to_datetime(series.loc[~iso_mask], errors="coerce", dayfirst=True)
    return out


def safe_div(numerator, denominator):
    try:
        if denominator in [0, None] or pd.isna(denominator):
            return 0.0
        return numerator / denominator
    except Exception:
        return 0.0


def extract_date_from_filename(filename: str):
    """Tenta extrair datas como 15.06, 15-06-2026, 2026-06-15 do nome do arquivo."""
    if not filename:
        return None
    patterns = [
        (r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})", "ymd"),
        (r"(\d{1,2})[-_.](\d{1,2})[-_.](20\d{2})", "dmy"),
        (r"(\d{1,2})[-_.](\d{1,2})(?!\d)", "dm"),
        # Aceita nomes como Finalização 0607.xlsx ou Distribuição 0707.xlsx.
        (r"(?:^|\D)(\d{2})(\d{2})(?:\D|$)", "dm_compacto"),
    ]
    for p, fmt in patterns:
        m = re.search(p, filename)
        if not m:
            continue
        g = m.groups()
        try:
            if fmt == "ymd":
                return pd.Timestamp(year=int(g[0]), month=int(g[1]), day=int(g[2]))
            if fmt == "dmy":
                return pd.Timestamp(year=int(g[2]), month=int(g[1]), day=int(g[0]))
            # Para arquivos diários sem ano, assume o ano corrente do ambiente.
            return pd.Timestamp(year=datetime.now().year, month=int(g[1]), day=int(g[0]))
        except Exception:
            continue
    return None


def format_currency(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_percent(value: float) -> str:
    return f"{value:.2%}".replace(".", ",")


def missing_columns(df: pd.DataFrame, required: Iterable[str]) -> list[str]:
    return [c for c in required if c not in df.columns]
