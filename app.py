from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import APP_TITLE, DB_PATH, TABLES
from src.exporters import to_excel_bytes
from src.io_utils import list_excel_sheets, read_named_sheets_from_workbook, read_table
from src.metrics import (
    OCC_TYPES,
    apply_base_month_rules,
    base_evolution_summary,
    base_scope_snapshot,
    base_scope_summary,
    classificar_regua_receita,
    cpf_unico,
    enrich_base_with_activity,
    enrich_datasets,
    filter_by_date,
    funil_by,
    geral_kpis,
    get_base_by_dates,
    latest_payment_import,
    novas_entradas,
    normalize_pagamentos_records,
    pagamentos_disappeared_from_latest,
    operadores,
    receita_by,
    receita_kpis,
)
from src.storage import clear_table, init_db, load_defaults_if_empty, load_table, save_dataframe, save_issues
from src.transformations import (
    normalize_depara_atraso,
    normalize_depara_ocorrencias,
    normalize_depara_operadores,
    transform_acionamentos,
    transform_base,
    transform_metas,
    transform_pagamentos,
)
from src.utils import extract_date_from_filename, format_currency, format_percent
from src.validators import validate_layout

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")

CSS = """
<style>
.main .block-container {padding-top: 1.3rem;}
.metric-card {padding: 16px; border-radius: 14px; background: #ffffff; border: 1px solid #e7e7e7; box-shadow: 0 1px 3px rgba(0,0,0,.05)}
.small-caption {font-size: 12px; color: #777;}
div[data-testid="stMetric"] {background: #fff; border: 1px solid #eee; border-radius: 12px; padding: 10px 12px;}
div[data-testid="stMetricValue"] {font-size: 1.45rem; line-height: 1.2; white-space: normal; overflow: visible;}
div[data-testid="stMetricLabel"] {font-size: .82rem;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

init_db()
load_defaults_if_empty()


@st.cache_data(show_spinner=False, ttl=120)
def _load_all_cached(db_mtime: float):
    depara_atraso = load_table(TABLES["depara_atraso"])
    depara_ocorrencias = load_table(TABLES["depara_ocorrencias"])
    depara_operadores = load_table(TABLES["depara_operadores"])
    base = load_table(TABLES["base"])
    pagamentos = load_table(TABLES["pagamentos"])
    acionamentos = load_table(TABLES["acionamentos"])
    return enrich_datasets(base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias, depara_operadores) + (depara_atraso, depara_ocorrencias)


def load_all():
    # O mtime do banco entra como argumento para invalidar o cache automaticamente
    # sempre que uma nova importação atualizar o arquivo solfacil.db.
    db_mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else 0.0
    return _load_all_cached(db_mtime)


def show_kpi(label: str, value: str, help_text: str | None = None):
    st.metric(label, value, help=help_text)



CURRENCY_COLS = {
    "VPL", "VPL_CONVERTIDO", "SALDO_VPL", "SALDO_EM_ATRASO", "VALOR_BRUTO",
    "VALOR_PAGO", "VALOR_PAGAMENTO", "HO", "H.O", "HO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL",
    "HO_PAGAMENTOS", "VALOR_PAGO_COMISSIONAVEL", "VALOR_PAGO_NAO_COMISSIONAVEL",
    "VPL_TOTAL", "VALOR_BRUTO_TOTAL", "SALDO_ATRASO_TOTAL", "VPL_DIA_ANTERIOR",
    "VPL_DIA_ATUAL", "VPL_ENTRADAS", "VPL_SAIDAS", "DELTA_VPL",
    "SALDO_VPL_CONTRATO", "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO",
    "SALDO_VPL_PAGO_SEM_ACIONAMENTO", "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO"
}
PERCENT_COLS = {"% CPC", "CONVERSAO", "% ATINGIDO", "PERC_CPC", "PERCENTUAL", "%"}


def _format_number_br(value, decimals: int = 0) -> str:
    try:
        n = float(value)
    except Exception:
        return "" if pd.isna(value) else str(value)
    if decimals == 0:
        return f"{n:,.0f}".replace(",", ".")
    return f"{n:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """Formata uma cópia do dataframe para exibição na tela, sem alterar os cálculos."""
    if df is None or df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        col_upper = str(col).upper()
        if col_upper in PERCENT_COLS or col_upper.startswith("%") or "CONVERS" in col_upper or "TAXA" in col_upper:
            if pd.api.types.is_numeric_dtype(out[col]) or pd.to_numeric(out[col], errors="coerce").notna().any():
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(format_percent)
        elif col_upper in CURRENCY_COLS or any(token in col_upper for token in ["VPL", "VALOR", "SALDO", "HO_"]):
            if pd.api.types.is_numeric_dtype(out[col]) or pd.to_numeric(out[col], errors="coerce").notna().any():
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(format_currency)
        elif "DATA" in col_upper:
            parsed = pd.to_datetime(out[col], errors="coerce")
            if parsed.notna().any():
                out[col] = parsed.dt.strftime("%d/%m/%Y").fillna("")
    return out


def _date_options(df: pd.DataFrame, preferred_cols: list[str]) -> list:
    """Retorna datas únicas, da mais recente para a mais antiga, usando a primeira coluna disponível."""
    if df is None or df.empty:
        return []
    for col in preferred_cols:
        if col in df.columns:
            dates = pd.to_datetime(df[col], errors="coerce").dropna().dt.date.unique()
            if len(dates):
                return sorted(dates, reverse=True)
    return []


def _period_options(dates: list) -> list[pd.Period]:
    periods = sorted({pd.Timestamp(d).to_period("M") for d in dates}, reverse=True)
    return periods


def _period_label(period: pd.Period) -> str:
    return pd.Timestamp(period.start_time).strftime("%m/%Y")


def _select_dates_by_month(label: str, dates: list, key: str, allow_multi: bool = True) -> list:
    if not dates:
        st.sidebar.caption(f"{label}: sem dados importados")
        return []
    periods = _period_options(dates)
    selected_period = st.sidebar.selectbox(
        f"{label} - mês",
        periods,
        index=0,
        key=f"{key}_mes",
        format_func=_period_label,
    )
    month_dates = sorted([d for d in dates if pd.Timestamp(d).to_period("M") == selected_period], reverse=True)
    if not allow_multi:
        selected = st.sidebar.selectbox(f"{label} - data", month_dates, index=0, key=f"{key}_data")
        return [selected]

    all_month = st.sidebar.checkbox(
        f"Selecionar todas as datas de {label.lower()} no mês",
        value=True,
        key=f"{key}_todas_mes",
    )
    if all_month:
        st.sidebar.caption(f"{len(month_dates)} importação(ões) selecionada(s) em {_period_label(selected_period)}.")
        return month_dates
    selected = st.sidebar.multiselect(
        f"{label} - datas",
        month_dates,
        default=month_dates[:1],
        key=f"{key}_datas",
        format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
    )
    return selected


def _filter_import_dates(df: pd.DataFrame, selected_dates, preferred_cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty or not selected_dates:
        return df
    selected = {pd.to_datetime(d).date() for d in selected_dates}
    for col in preferred_cols:
        if col in df.columns:
            out = df.copy()
            out[col] = pd.to_datetime(out[col], errors="coerce")
            return out[out[col].dt.date.isin(selected)].copy()
    return df


def _fmt_filter_date(value) -> str:
    if value is None or value == []:
        return "sem dados"
    if value == "Todas":
        return "Todas"
    if isinstance(value, (list, tuple, set)):
        vals = sorted([pd.to_datetime(v).date() for v in value])
        if not vals:
            return "sem dados"
        if len(vals) == 1:
            return pd.Timestamp(vals[0]).strftime("%d/%m/%Y")
        return f"{len(vals)} datas ({pd.Timestamp(vals[0]).strftime('%d/%m/%Y')} a {pd.Timestamp(vals[-1]).strftime('%d/%m/%Y')})"
    return pd.to_datetime(value).strftime("%d/%m/%Y")


def _latest_selected_date(value):
    if isinstance(value, (list, tuple, set)) and value:
        return max(pd.to_datetime(v).date() for v in value)
    if value in [None, [], "Todas"]:
        return None
    return pd.to_datetime(value).date()



def _month_dates_for_period(dates: list, period: pd.Period | None) -> list:
    if not dates or period is None:
        return []
    return sorted([d for d in dates if pd.Timestamp(d).to_period("M") == period], reverse=True)


def _latest_date_in_month(dates: list, period: pd.Period | None):
    month_dates = _month_dates_for_period(dates, period)
    return month_dates[0] if month_dates else None


def sidebar_filters(base: pd.DataFrame, pagamentos: pd.DataFrame, acionamentos: pd.DataFrame):
    st.sidebar.header("Filtros das bases importadas")
    st.sidebar.caption("A base de clientes define o mês e a data de referência. Pagamentos usam, por padrão, a última base importada do mês.")

    base_dates = _date_options(base, ["DATA_BASE", "DATA_IMPORTACAO", "IMPORTED_AT"])
    pagamento_dates = _date_options(pagamentos, ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_PAGAMENTO_BOLETO"])
    acionamento_import_dates = _date_options(acionamentos, ["DATA_IMPORTACAO", "IMPORTED_AT"])

    if not base_dates:
        return {
            "base_clientes": [],
            "base_ref_date": None,
            "base_scope": "Base Ativa do mês",
            "pagamentos": [],
            "pagamentos_modo": "Última base de pagamento do mês",
            "acionamentos": [],
            "acionamentos_modo": "Todo mês da base",
        }

    base_periods = _period_options(base_dates)
    selected_period = st.sidebar.selectbox(
        "Mês da carteira",
        base_periods,
        index=0,
        key="filtro_mes_carteira",
        format_func=_period_label,
    )
    month_base_dates = _month_dates_for_period(base_dates, selected_period)
    ref_date = st.sidebar.selectbox(
        "Data de referência da carteira",
        month_base_dates,
        index=0,
        key="filtro_data_ref_carteira",
        format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
        help="Normalmente é a última base diária importada. Ela define a base ativa do dia e o limite dos cálculos do mês.",
    )

    base_scope = st.sidebar.radio(
        "Visão da base para funil/resultado",
        ["Base Ativa do mês", "Base Total do mês", "Base Congelada", "Clientes Entrantes"],
        index=0,
        key="filtro_escopo_base",
        help=(
            "Base Ativa = última importação do mês. Base Total = todos os ID_FIN que passaram no mês. "
            "Base Congelada = base da meta do cliente. Entrantes = novos ID_FIN a partir do dia 02."
        ),
    )

    # Pagamentos: controle oficial deve ser a última base de pagamentos importada no mês.
    pagamento_periods = _period_options(pagamento_dates) if pagamento_dates else []
    default_pg_period_idx = 0
    if pagamento_periods and selected_period in pagamento_periods:
        default_pg_period_idx = pagamento_periods.index(selected_period)
    pg_period = st.sidebar.selectbox(
        "Mês da base de pagamentos",
        pagamento_periods if pagamento_periods else [selected_period],
        index=default_pg_period_idx if pagamento_periods else 0,
        key="filtro_mes_pagamentos",
        format_func=_period_label,
    )
    pg_month_dates = _month_dates_for_period(pagamento_dates, pg_period)
    pg_mode = st.sidebar.radio(
        "Controle de pagamento",
        ["Última base de pagamento do mês", "Escolher importações", "Histórico do mês sem duplicar"],
        index=0,
        key="filtro_modo_pagamento",
        help="Use a última base como controle oficial. As bases anteriores ficam guardadas para auditoria de pagamentos que sumiram da última importação.",
    )
    if pg_mode == "Última base de pagamento do mês":
        selected_pg_dates = [pg_month_dates[0]] if pg_month_dates else []
        if selected_pg_dates:
            st.sidebar.caption(f"Pagamento oficial: {pd.Timestamp(selected_pg_dates[0]).strftime('%d/%m/%Y')}.")
    elif pg_mode == "Histórico do mês sem duplicar":
        selected_pg_dates = pg_month_dates
    else:
        selected_pg_dates = st.sidebar.multiselect(
            "Datas de importação de pagamentos",
            pg_month_dates,
            default=pg_month_dates[:1],
            key="filtro_datas_pagamento_manual",
            format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
        )

    # Acionamento: a base recebida é consolidada no mês. Depois da importação deduplicada,
    # a visão correta é por DATA_ACIONAMENTO dentro do mês da carteira, não por importação.
    ac_mode = st.sidebar.radio(
        "Base de acionamentos",
        ["Todo mês da base", "Escolher importações"],
        index=0,
        key="filtro_modo_acionamento",
        help="Como o arquivo de finalização é consolidado do mês, o padrão considera todos os acionamentos únicos do mês da base.",
    )
    if ac_mode == "Todo mês da base":
        selected_ac_dates = []
    else:
        ac_periods = _period_options(acionamento_import_dates) if acionamento_import_dates else []
        default_ac_idx = ac_periods.index(selected_period) if ac_periods and selected_period in ac_periods else 0
        ac_period = st.sidebar.selectbox(
            "Mês da importação de acionamentos",
            ac_periods if ac_periods else [selected_period],
            index=default_ac_idx if ac_periods else 0,
            key="filtro_mes_acionamentos",
            format_func=_period_label,
        )
        ac_month_dates = _month_dates_for_period(acionamento_import_dates, ac_period)
        selected_ac_dates = st.sidebar.multiselect(
            "Datas de importação de acionamentos",
            ac_month_dates,
            default=ac_month_dates[:1],
            key="filtro_datas_acionamento_manual",
            format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
        )

    return {
        "base_clientes": month_base_dates,
        "base_ref_date": ref_date,
        "base_month": selected_period,
        "base_scope": base_scope,
        "pagamentos": selected_pg_dates,
        "pagamentos_modo": pg_mode,
        "acionamentos": selected_ac_dates,
        "acionamentos_modo": ac_mode,
    }


def _filter_acionamentos_mes_base(acionamentos: pd.DataFrame, ref_date) -> pd.DataFrame:
    if acionamentos is None or acionamentos.empty or "DATA_ACIONAMENTO" not in acionamentos.columns:
        return pd.DataFrame(columns=acionamentos.columns if acionamentos is not None else [])
    ref = _latest_selected_date([ref_date]) if ref_date else None
    if ref is None:
        return acionamentos
    period = pd.Timestamp(ref).to_period("M")
    out = acionamentos.copy()
    out["DATA_ACIONAMENTO"] = pd.to_datetime(out["DATA_ACIONAMENTO"], errors="coerce")
    out = out[out["DATA_ACIONAMENTO"].dt.to_period("M") == period].copy()
    out = out[out["DATA_ACIONAMENTO"].dt.date <= ref].copy()
    return out


def apply_basic_filters(base: pd.DataFrame, pagamentos: pd.DataFrame, acionamentos: pd.DataFrame, filters):
    if not isinstance(filters, dict):
        filters = {"base_clientes": [filters], "base_ref_date": filters, "pagamentos": None, "acionamentos": None, "base_scope": "Base Ativa do mês"}

    ref_date = filters.get("base_ref_date") or _latest_selected_date(filters.get("base_clientes"))
    try:
        base_congelada = load_table(TABLES.get("base_congelada", "base_congelada"))
    except Exception:
        base_congelada = pd.DataFrame()

    base_dia = base_scope_snapshot(base, ref_date=ref_date, scope=filters.get("base_scope", "Base Ativa do mês"), base_congelada=base_congelada)

    if filters.get("pagamentos_modo") == "Última base de pagamento do mês":
        pagamentos = latest_payment_import(pagamentos, ref_date=ref_date)
    else:
        pagamentos = _filter_import_dates(pagamentos, filters.get("pagamentos"), ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_PAGAMENTO_BOLETO"])
        pagamentos = normalize_pagamentos_records(pagamentos)

    if filters.get("acionamentos_modo") == "Todo mês da base":
        acionamentos = _filter_acionamentos_mes_base(acionamentos, ref_date)
    else:
        acionamentos = _filter_import_dates(acionamentos, filters.get("acionamentos"), ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_ACIONAMENTO"])

    with st.sidebar.expander("Filtros avançados de acionamento", expanded=False):
        if acionamentos is not None and not acionamentos.empty:
            if "CANAL_ACIONAMENTO" in acionamentos.columns:
                canais = sorted([x for x in acionamentos["CANAL_ACIONAMENTO"].dropna().astype(str).unique() if x != ""])
                canal_sel = st.multiselect("Canal de atendimento", canais, key="filtro_canal_acionamento")
                if canal_sel:
                    acionamentos = acionamentos[acionamentos["CANAL_ACIONAMENTO"].astype(str).isin(canal_sel)].copy()
            if "ORIGEM_ACIONAMENTO" in acionamentos.columns:
                origens = sorted([x for x in acionamentos["ORIGEM_ACIONAMENTO"].dropna().astype(str).unique() if x != ""])
                origem_sel = st.multiselect("Origem do operador", origens, key="filtro_origem_operador")
                if origem_sel:
                    acionamentos = acionamentos[acionamentos["ORIGEM_ACIONAMENTO"].astype(str).isin(origem_sel)].copy()

    with st.sidebar.expander("Filtros avançados da base selecionada", expanded=False):
        if not base_dia.empty:
            celula_filter_col = "CELULA_VISAO" if "CELULA_VISAO" in base_dia.columns else "CELULA"
            if celula_filter_col in base_dia.columns:
                opts = sorted([x for x in base_dia[celula_filter_col].dropna().unique()])
                selected = st.multiselect("Célula / visão", opts)
                if selected:
                    base_dia = base_dia[base_dia[celula_filter_col].isin(selected)]
            if "FAIXA_ATRASO" in base_dia.columns:
                opts = sorted([x for x in base_dia["FAIXA_ATRASO"].dropna().unique()])
                selected = st.multiselect("Faixa de atraso", opts)
                if selected:
                    base_dia = base_dia[base_dia["FAIXA_ATRASO"].isin(selected)]
            if "FUNDO" in base_dia.columns:
                opts = sorted([x for x in base_dia["FUNDO"].dropna().unique()])
                selected = st.multiselect("Fundo", opts)
                if selected:
                    base_dia = base_dia[base_dia["FUNDO"].isin(selected)]
            if "CARTEIRA" in base_dia.columns:
                opts = sorted([x for x in base_dia["CARTEIRA"].dropna().unique()])
                selected = st.multiselect("Carteira", opts)
                if selected:
                    base_dia = base_dia[base_dia["CARTEIRA"].isin(selected)]

    base_dia, pagamentos, acionamentos = apply_base_month_rules(base, base_dia, pagamentos, acionamentos, ref_date)
    pagamentos = classificar_regua_receita(base_dia, pagamentos, acionamentos)
    return base_dia, pagamentos, acionamentos


def build_pagamentos_oficial_mes(base: pd.DataFrame, pagamentos_raw: pd.DataFrame, acionamentos: pd.DataFrame, ref_date):
    """Monta a visão oficial de pagamentos do mês.

    Usa a última base de pagamentos importada no mês como controle oficial,
    sem limitar ao escopo ativo da carteira. O objetivo é conciliar o total do
    arquivo e separar os pagamentos de ID_FIN com/sem acionamento BL no mês.
    """
    base_total = base_scope_snapshot(base, ref_date=ref_date, scope="Base Total do mês")
    pagamentos_oficial = latest_payment_import(pagamentos_raw, ref_date=ref_date)
    acionamentos_mes = _filter_acionamentos_mes_base(acionamentos, ref_date)
    # Mantém todos os pagamentos do arquivo oficial; usa a base total apenas como contexto para célula/faixa/VPL.
    pagamentos_oficial = normalize_pagamentos_records(pagamentos_oficial)
    pagamentos_oficial = classificar_regua_receita(base_total, pagamentos_oficial, acionamentos_mes)
    return base_total, pagamentos_oficial, acionamentos_mes

def page_importar():
    st.title("Importar arquivos")
    st.caption("Importe Base, Pagamentos, Acionamento, DePara e Metas. O sistema valida layout e atualiza o banco local.")

    import_kind = st.selectbox(
        "Tipo de importação",
        [
            "Arquivo completo com abas Base/Pagamentos/Acionamento/DePara",
            "Base",
            "Pagamentos",
            "Acionamento",
            "Base Congelada",
            "DePara Ocorrências",
            "DePara Atraso / Taxa H.O.",
            "DePara Operadores",
            "Metas",
        ],
    )
    file = st.file_uploader("Selecione o arquivo", type=["xlsx", "xlsm", "xls", "csv"])

    inferred_date = extract_date_from_filename(file.name) if file else None
    data_base = st.date_input(
        "Data de referência/importação do arquivo",
        value=inferred_date.date() if inferred_date is not None else date.today(),
        help="Essa data ficará gravada nos registros importados. No Dashboard Geral, você poderá escolher separadamente a data da Base de Clientes, da Base de Pagamentos e da Base de Acionamentos.",
    )
    mode_label = st.radio(
        "Modo de gravação",
        ["Acrescentar/atualizar sem duplicar", "Substituir tabela inteira", "Substituir mesma data da base"],
        horizontal=True,
    )
    mode = {"Acrescentar/atualizar sem duplicar": "append_dedup", "Substituir tabela inteira": "replace_all", "Substituir mesma data da base": "replace_same_data_base"}[mode_label]

    manual_sheet_name = None
    if file and not import_kind.startswith("Arquivo completo") and Path(file.name).suffix.lower() in [".xlsx", ".xls", ".xlsm"]:
        sheets = list_excel_sheets(file)
        manual_sheet_name = st.selectbox("Aba do Excel", sheets) if sheets else None
        try:
            file.seek(0)
        except Exception:
            pass

    if not file:
        st.info("Envie um arquivo para iniciar.")
        return

    if st.button("Validar e importar", type="primary"):
        try:
            depara_atraso = load_table(TABLES["depara_atraso"])
            depara_ocorrencias = load_table(TABLES["depara_ocorrencias"])
            depara_operadores = load_table(TABLES["depara_operadores"])

            if import_kind.startswith("Arquivo completo"):
                dfs = read_named_sheets_from_workbook(file)
                if not dfs:
                    st.error("Não consegui localizar as abas Base, Pagamentos, Acionamento ou DePara no arquivo.")
                    return
                imported = []
                if "depara" in dfs:
                    depara_df = dfs["depara"]
                    # No layout do arquivo original: D/E = ocorrências, G:K = faixas/taxas, A/B = operadores.
                    occ_cols = [c for c in ["FINALIZACAO", "TIPO_OCORRENCIA"] if c in depara_df.columns]
                    if occ_cols:
                        occ = normalize_depara_ocorrencias(depara_df[occ_cols])
                        save_dataframe(occ, TABLES["depara_ocorrencias"], file.name, mode="replace_all")
                    atraso_cols = [c for c in ["DE", "ATE", "FAIXA_ATRASO", "CELULA", "TAXA_HO"] if c in depara_df.columns]
                    if {"DE", "ATE"}.issubset(set(atraso_cols)):
                        at = normalize_depara_atraso(depara_df[atraso_cols])
                        save_dataframe(at, TABLES["depara_atraso"], file.name, mode="replace_all")
                    depara_atraso = load_table(TABLES["depara_atraso"])
                    depara_ocorrencias = load_table(TABLES["depara_ocorrencias"])

                if "base" in dfs:
                    df = transform_base(dfs["base"], data_base=data_base, depara_atraso=depara_atraso)
                    ok, issues = validate_layout(df, "base")
                    if ok:
                        bid = save_dataframe(df, TABLES["base"], file.name, mode=mode, key_cols=["DATA_BASE", "ID_FIN"], data_base=str(data_base))
                        save_issues(issues, bid)
                        imported.append(("Base", len(df)))
                    else:
                        st.error("Base rejeitada por erro crítico.")
                        st.dataframe(issues)
                if "base_congelada" in dfs:
                    df = transform_base(dfs["base_congelada"], data_base=data_base, depara_atraso=depara_atraso)
                    ok, issues = validate_layout(df, "base")
                    if ok:
                        bid = save_dataframe(df, TABLES["base_congelada"], file.name, mode=mode, key_cols=["DATA_BASE", "ID_FIN"], data_base=str(data_base))
                        save_issues(issues, bid)
                        imported.append(("Base Congelada", len(df)))
                    else:
                        st.error("Base Congelada rejeitada por erro crítico.")
                        st.dataframe(issues)
                if "pagamentos" in dfs:
                    df = transform_pagamentos(dfs["pagamentos"], depara_atraso=depara_atraso)
                    ok, issues = validate_layout(df, "pagamentos")
                    if ok:
                        bid = save_dataframe(df, TABLES["pagamentos"], file.name, mode=mode, key_cols=["DATA_IMPORTACAO", "ID_FIN", "PARCELA", "DATA_PAGAMENTO_BOLETO", "VALOR_PAGAMENTO"], data_base=str(data_base))
                        save_issues(issues, bid)
                        imported.append(("Pagamentos", len(df)))
                    else:
                        st.error("Pagamentos rejeitado por erro crítico.")
                        st.dataframe(issues)
                if "acionamentos" in dfs:
                    df = transform_acionamentos(dfs["acionamentos"], depara_ocorrencias=depara_ocorrencias, depara_operadores=depara_operadores)
                    ok, issues = validate_layout(df, "acionamentos")
                    if ok:
                        bid = save_dataframe(df, TABLES["acionamentos"], file.name, mode=mode, key_cols=["COD_HISTO", "ID_FIN", "DATA_ACIONAMENTO", "FINALIZACAO", "RESPONSAVEL"], data_base=str(data_base))
                        save_issues(issues, bid)
                        imported.append(("Acionamento", len(df)))
                    else:
                        st.error("Acionamento rejeitado por erro crítico.")
                        st.dataframe(issues)
                _load_all_cached.clear()
                st.success("Importação concluída: " + ", ".join([f"{n}: {qtd:,}" for n, qtd in imported]).replace(",", "."))
                return

            data_type_map = {
                "Base": "base",
                "Pagamentos": "pagamentos",
                "Acionamento": "acionamentos",
                "Base Congelada": "base_congelada",
                "DePara Ocorrências": "depara_ocorrencias",
                "DePara Atraso / Taxa H.O.": "depara_atraso",
                "DePara Operadores": "depara_operadores",
                "Metas": "metas",
            }
            data_type = data_type_map[import_kind]
            df = read_table(file, sheet_name=manual_sheet_name)
            if data_type == "base":
                df = transform_base(df, data_base=data_base, depara_atraso=depara_atraso)
                key_cols = ["DATA_BASE", "ID_FIN"]
            elif data_type == "base_congelada":
                df = transform_base(df, data_base=data_base, depara_atraso=depara_atraso)
                key_cols = ["DATA_BASE", "ID_FIN"]
            elif data_type == "pagamentos":
                df = transform_pagamentos(df, depara_atraso=depara_atraso)
                key_cols = ["DATA_IMPORTACAO", "ID_FIN", "PARCELA", "DATA_PAGAMENTO_BOLETO", "VALOR_PAGAMENTO"]
            elif data_type == "acionamentos":
                df = transform_acionamentos(df, depara_ocorrencias=depara_ocorrencias, depara_operadores=depara_operadores)
                key_cols = ["DATA_IMPORTACAO", "COD_HISTO", "ID_FIN", "DATA_ACIONAMENTO", "FINALIZACAO", "RESPONSAVEL"]
            elif data_type == "depara_ocorrencias":
                df = normalize_depara_ocorrencias(df)
                key_cols = ["FINALIZACAO"]
            elif data_type == "depara_atraso":
                df = normalize_depara_atraso(df)
                key_cols = ["DE", "ATE", "FAIXA_ATRASO"]
            elif data_type == "depara_operadores":
                df = normalize_depara_operadores(df)
                key_cols = ["RESPONSAVEL"]
            elif data_type == "metas":
                df = transform_metas(df)
                key_cols = ["CELULA"]
            else:
                key_cols = []

            ok, issues = validate_layout(df, "base" if data_type == "base_congelada" else data_type if data_type in ["base", "pagamentos", "acionamentos", "depara_ocorrencias", "depara_atraso"] else "")
            if not ok:
                st.error("Arquivo rejeitado por erro crítico.")
                st.dataframe(issues, use_container_width=True)
                return
            batch_id = save_dataframe(df, TABLES[data_type], file.name, mode=mode, key_cols=key_cols, data_base=str(data_base))
            save_issues(issues, batch_id)
            _load_all_cached.clear()
            st.success(f"Importado com sucesso: {len(df):,} linhas".replace(",", "."))
            if not issues.empty:
                st.warning("Importado com alertas. Veja as inconsistências abaixo.")
                st.dataframe(issues, use_container_width=True)
        except Exception as e:
            st.exception(e)


def page_dashboard():
    st.title("Dashboard Geral")
    with st.spinner("Carregando bases e recalculando indicadores..."):
        base, pagamentos_raw, acionamentos, depara_atraso, depara_ocorrencias = load_all()
        filtros_datas = sidebar_filters(base, pagamentos_raw, acionamentos)
        if filtros_datas.get("pagamentos_modo") == "Última base de pagamento do mês":
            pagamentos_importados = latest_payment_import(pagamentos_raw, filtros_datas.get("base_ref_date"))
        else:
            pagamentos_importados = normalize_pagamentos_records(
                _filter_import_dates(pagamentos_raw, filtros_datas.get("pagamentos"), ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_PAGAMENTO_BOLETO"])
            )
        try:
            base_congelada = load_table(TABLES.get("base_congelada", "base_congelada"))
        except Exception:
            base_congelada = pd.DataFrame()
        resumo_base_mes = base_scope_summary(base, filtros_datas.get("base_ref_date"), base_congelada=base_congelada)
        pagamentos_desaparecidos = pagamentos_disappeared_from_latest(pagamentos_raw, filtros_datas.get("base_ref_date"))
        base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos_raw, acionamentos, filtros_datas)
    st.caption(
        "Bases utilizadas nos indicadores: "
        f"Visão {filtros_datas.get('base_scope')} | "
        f"Clientes ref. {_fmt_filter_date(filtros_datas.get('base_ref_date'))} | "
        f"Pagamentos {_fmt_filter_date(filtros_datas.get('pagamentos'))} | "
        f"Acionamentos {filtros_datas.get('acionamentos_modo')}. "
        "Regra aplicada: eventos somente dentro do mês da base, após a entrada do ID_FIN, com acionamentos únicos de operadores."
    )
    base_enriched = enrich_base_with_activity(base_dia, pagamentos, acionamentos)
    k = geral_kpis(base_enriched, pagamentos, acionamentos)
    rk = receita_kpis(pagamentos)
    valor_pago_importado = pd.to_numeric(pagamentos_importados.get("VALOR_PAGAMENTO", pd.Series(dtype=float)), errors="coerce").fillna(0).sum() if pagamentos_importados is not None and not pagamentos_importados.empty else 0.0

    st.subheader("Composição da carteira no mês")
    c = st.columns(6)
    c[0].metric("Base ativa do mês", _format_number_br(resumo_base_mes.get("base_ativa_mes", 0)))
    c[1].metric("Base total do mês", _format_number_br(resumo_base_mes.get("base_total_mes", 0)), help="Quantidade unique de ID_FIN que passou pela carteira no mês, incluindo devolvidos.")
    c[2].metric("CPFs total do mês", _format_number_br(resumo_base_mes.get("cpfs_total_mes", 0)))
    c[3].metric("Base congelada", _format_number_br(resumo_base_mes.get("base_congelada", 0)))
    c[4].metric("Clientes entrantes", _format_number_br(resumo_base_mes.get("clientes_entrantes", 0)))
    c[5].metric("Devolvidos no mês", _format_number_br(resumo_base_mes.get("devolvidos_mes", 0)))

    st.subheader("Indicadores da visão selecionada")
    c = st.columns(4)
    c[0].metric("Contratos ID_FIN", _format_number_br(k["contratos"]))
    c[1].metric("CPF/CNPJ únicos", _format_number_br(k["cpfs"]))
    c[2].metric("Saldo VPL", format_currency(k["vpl"]))
    c[3].metric("Valor bruto", format_currency(k["valor_bruto"]))

    c = st.columns(4)
    c[0].metric("Valor Pago Total Arquivo", format_currency(valor_pago_importado), help="Soma do VALOR_PAGAMENTO da(s) base(s) de pagamentos selecionada(s), após remover duplicidades do próprio arquivo.")
    c[1].metric("Valor Pago Elegível Régua", format_currency(k["valor_pago"]), help="Pagamentos dentro do mês da base e após a entrada do ID_FIN.")
    c[2].metric("Saldo VPL Total Pago", format_currency(rk["saldo_vpl_pago_total"]), help="Saldo VPL dos contratos pagos, contado uma vez por ID_FIN. Não é base de comissão.")
    c[3].metric("H.O. Total Elegível", format_currency(k["ho"]))

    c = st.columns(4)
    c[0].metric("Saldo VPL pago com acionamento", format_currency(rk["saldo_vpl_pago_com_acionamento"]))
    c[1].metric("Valor pago com acionamento", format_currency(rk["valor_pago_com_acionamento"]))
    c[2].metric("Valor pago sem acionamento", format_currency(rk["valor_pago_sem_acionamento"]))
    c[3].metric("Pagamentos comissionáveis", _format_number_br(rk["pagamentos_comissionaveis"]))

    c = st.columns(4)
    c[0].metric("Tentativas", _format_number_br(k["tentativa"]))
    c[1].metric("Alô", _format_number_br(k["alo"]))
    c[2].metric("CPC", _format_number_br(k["cpc"]))
    c[3].metric("% CPC", format_percent(k["perc_cpc"]))

    c = st.columns(4)
    c[0].metric("CPCA", _format_number_br(k["cpca"]))
    c[1].metric("Acordos", _format_number_br(k["acordo"]))
    c[2].metric("Conversão", format_percent(k["conversao"]))
    c[3].metric("H.O. comissionável", format_currency(rk["ho_comissionavel"]))

    st.subheader("Régua de receita / comissionamento")
    st.caption("Receita e H.O. são calculados sobre VALOR_PAGAMENTO real. Saldo VPL permanece apenas para carteira, produção e meta do cliente.")
    c = st.columns(4)
    c[0].metric("Valor comissionável", format_currency(rk["valor_pago_comissionavel"]))
    c[1].metric("H.O. comissionável", format_currency(rk["ho_comissionavel"]))
    c[2].metric("Valor não comissionável", format_currency(rk["valor_pago_nao_comissionavel"]))
    c[3].metric("H.O. não comissionável", format_currency(rk["ho_nao_comissionavel"]))

    resumo_receita = receita_by(pagamentos, "TIPO_RECEITA")
    if not resumo_receita.empty:
        st.dataframe(format_display_df(resumo_receita), use_container_width=True)

    if pagamentos_desaparecidos is not None and not pagamentos_desaparecidos.empty:
        with st.expander("Pagamentos que existiam em bases anteriores e sumiram da última base de pagamentos"):
            st.caption("A última base de pagamentos é o controle oficial. Esta visão mantém auditoria dos ID_FIN pagos que apareceram antes e não aparecem na última importação do mês.")
            st.dataframe(format_display_df(pagamentos_desaparecidos), use_container_width=True)

    st.subheader("Resumo por célula")
    celula_group_col = "CELULA_VISAO" if "CELULA_VISAO" in base_enriched.columns else "CELULA"
    funil = funil_by(base_enriched, celula_group_col)
    if not funil.empty and celula_group_col != "CELULA":
        funil = funil.rename(columns={celula_group_col: "CELULA"})
    receita_group_col = "CELULA_VISAO" if pagamentos is not None and not pagamentos.empty and "CELULA_VISAO" in pagamentos.columns else "CELULA"
    receita_celula = receita_by(pagamentos, receita_group_col) if pagamentos is not None and not pagamentos.empty and receita_group_col in pagamentos.columns else pd.DataFrame()
    if not receita_celula.empty and receita_group_col != "CELULA":
        receita_celula = receita_celula.rename(columns={receita_group_col: "CELULA"})
    if not funil.empty and not receita_celula.empty:
        pay_cols = [
            "CELULA", "VALOR_PAGO", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL",
            "VALOR_PAGO_COM_ACIONAMENTO", "VALOR_PAGO_SEM_ACIONAMENTO",
            "SALDO_VPL_TOTAL_PAGO", "SALDO_VPL_PAGO_COM_ACIONAMENTO", "SALDO_VPL_PAGO_SEM_ACIONAMENTO"
        ]
        merge_pay = receita_celula[[c for c in pay_cols if c in receita_celula.columns]].copy()
        funil = funil.drop(columns=[c for c in pay_cols if c != "CELULA" and c in funil.columns], errors="ignore").merge(merge_pay, on="CELULA", how="left")
        for c in [c for c in pay_cols if c != "CELULA"]:
            if c in funil.columns:
                funil[c] = pd.to_numeric(funil[c], errors="coerce").fillna(0)
    if funil.empty:
        st.info("Sem dados para exibir. Importe a base primeiro.")
        return
    st.dataframe(format_display_df(funil), use_container_width=True)
    left, right = st.columns(2)
    with left:
        fig = px.bar(funil, x="CELULA", y="SALDO_VPL", title="Saldo VPL por célula", text_auto=".2s")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        funil_long = funil.melt(
            id_vars=["CELULA"],
            value_vars=[c for c in OCC_TYPES if c in funil.columns],
            var_name="Etapa",
            value_name="Quantidade",
        )
        fig = px.bar(funil_long, x="CELULA", y="Quantidade", color="Etapa", title="Funil por célula", barmode="group")
        st.plotly_chart(fig, use_container_width=True)


def page_funil():
    st.title("Funil de Acionamento")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos, acionamentos)
    base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos, acionamentos, filtros_datas)
    base_enriched = enrich_base_with_activity(base_dia, pagamentos, acionamentos)
    st.caption(f"Acionamentos únicos considerados no funil: {len(acionamentos):,}".replace(",", "."))
    group_label = st.radio("Agrupar por", ["CELULA", "FAIXA_ATRASO", "FUNDO", "CARTEIRA"], horizontal=True)
    group_col = "CELULA_VISAO" if group_label == "CELULA" and "CELULA_VISAO" in base_enriched.columns else group_label
    funil = funil_by(base_enriched, group_col)
    if not funil.empty and group_col != group_label:
        funil = funil.rename(columns={group_col: group_label})
        group_col = group_label
    if funil.empty:
        st.info("Sem dados para exibir.")
        return
    st.dataframe(format_display_df(funil), use_container_width=True)
    fig = px.funnel(funil.sort_values("UNIQUE", ascending=False), x="UNIQUE", y=group_col, title="Volume de contratos por grupo")
    st.plotly_chart(fig, use_container_width=True)
    fig2 = px.bar(funil, x=group_col, y="SEM_TENTATIVA", title="Contratos sem tentativa")
    st.plotly_chart(fig2, use_container_width=True)


def page_pagamentos():
    st.title("Pagamentos, H.O. e Régua de Receita")
    base, pagamentos_raw, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos_raw, acionamentos)
    if filtros_datas.get("pagamentos_modo") == "Última base de pagamento do mês":
        pagamentos_importados = latest_payment_import(pagamentos_raw, filtros_datas.get("base_ref_date"))
    else:
        pagamentos_importados = normalize_pagamentos_records(
            _filter_import_dates(pagamentos_raw, filtros_datas.get("pagamentos"), ["DATA_IMPORTACAO", "IMPORTED_AT", "DATA_PAGAMENTO_BOLETO"])
        )
    pagamentos_desaparecidos = pagamentos_disappeared_from_latest(pagamentos_raw, filtros_datas.get("base_ref_date"))
    # Para a página de Pagamentos/H.O., a conciliação deve partir do arquivo oficial de pagamentos.
    # O escopo da base selecionado no menu continua valendo para funil/resultado, mas aqui não limita o total financeiro.
    base_total_mes, pagamentos, acionamentos_mes = build_pagamentos_oficial_mes(base, pagamentos_raw, acionamentos, filtros_datas.get("base_ref_date"))
    if pagamentos.empty:
        st.info("Sem pagamentos na última base de pagamento oficial do mês selecionado.")
        return

    start, end = st.date_input(
        "Período de pagamento",
        value=(
            pd.to_datetime(pagamentos["DATA_PAGAMENTO_BOLETO"], errors="coerce").min().date(),
            pd.to_datetime(pagamentos["DATA_PAGAMENTO_BOLETO"], errors="coerce").max().date(),
        ),
    ) if "DATA_PAGAMENTO_BOLETO" in pagamentos.columns else (None, None)
    pagamentos_f = filter_by_date(pagamentos, "DATA_PAGAMENTO_BOLETO", start, end) if start else pagamentos

    rk = receita_kpis(pagamentos_f)
    valor_pago_importado = pd.to_numeric(pagamentos_importados.get("VALOR_PAGAMENTO", pd.Series(dtype=float)), errors="coerce").fillna(0).sum() if pagamentos_importados is not None and not pagamentos_importados.empty else 0.0
    ticket_medio = pd.to_numeric(pagamentos_f["VALOR_PAGAMENTO"], errors="coerce").fillna(0).mean() if "VALOR_PAGAMENTO" in pagamentos_f.columns and not pagamentos_f.empty else 0
    cols = st.columns(6)
    cols[0].metric("Valor pago total arquivo", format_currency(valor_pago_importado))
    cols[1].metric("Valor pago elegível", format_currency(rk["valor_pago_total"]))
    cols[2].metric("Saldo VPL total pago", format_currency(rk["saldo_vpl_pago_total"]))
    cols[3].metric("Valor com acionamento", format_currency(rk["valor_pago_com_acionamento"]))
    cols[4].metric("Valor sem acionamento", format_currency(rk["valor_pago_sem_acionamento"]))
    cols[5].metric("Contratos pagos", f"{rk['contratos_pagos']:,}".replace(",", "."))

    cols = st.columns(6)
    cols[0].metric("H.O. elegível", format_currency(rk["ho_total"]))
    cols[1].metric("Valor comissionável", format_currency(rk["valor_pago_comissionavel"]))
    cols[2].metric("H.O. comissionável", format_currency(rk["ho_comissionavel"]))
    cols[3].metric("Saldo VPL com acionamento", format_currency(rk["saldo_vpl_pago_com_acionamento"]))
    cols[4].metric("Saldo VPL sem acionamento", format_currency(rk["saldo_vpl_pago_sem_acionamento"]))
    cols[5].metric("Ticket médio", format_currency(ticket_medio))

    st.caption(
        "Conciliação: Valor pago total usa a última base oficial de pagamentos do mês. "
        "Valor com/sem acionamento separa ID_FIN que tiveram qualquer acionamento BL válido no mês. "
        "Para virar receita comissionável, continua sendo exigido ACORDO BL antes do pagamento ou acionamento válido antes do pagamento. "
        "H.O. é calculado sobre VALOR_PAGAMENTO real, nunca sobre Saldo VPL."
    )

    if pagamentos_desaparecidos is not None and not pagamentos_desaparecidos.empty:
        with st.expander("Auditoria: pagamentos que sumiram da última base importada"):
            st.dataframe(format_display_df(pagamentos_desaparecidos), use_container_width=True)

    resumo = receita_by(pagamentos_f, "TIPO_RECEITA")
    if not resumo.empty:
        st.subheader("Resumo da régua de receita")
        st.dataframe(format_display_df(resumo), use_container_width=True)
        resumo_long = resumo.melt(id_vars=["TIPO_RECEITA"], value_vars=["VALOR_PAGO", "VALOR_PAGO_COMISSIONAVEL", "VALOR_PAGO_NAO_COMISSIONAVEL"], var_name="Indicador", value_name="Valor")
        st.plotly_chart(px.bar(resumo_long, x="TIPO_RECEITA", y="Valor", color="Indicador", barmode="group", title="Valor pago por tipo de receita"), use_container_width=True)

    if "DATA_PAGAMENTO_BOLETO" in pagamentos_f.columns:
        evol = pagamentos_f.copy()
        evol["DIA"] = pd.to_datetime(evol["DATA_PAGAMENTO_BOLETO"], errors="coerce").dt.date
        evol = evol.groupby("DIA", as_index=False).agg(
            VALOR_PAGAMENTO=("VALOR_PAGAMENTO", "sum"),
            HO=("HO", "sum"),
            VALOR_PAGO_COMISSIONAVEL=("VALOR_PAGO_COMISSIONAVEL", "sum"),
            HO_COMISSIONAVEL=("HO_COMISSIONAVEL", "sum"),
        )
        evol_long = evol.melt(id_vars=["DIA"], value_vars=["VALOR_PAGAMENTO", "VALOR_PAGO_COMISSIONAVEL", "HO_COMISSIONAVEL"], var_name="Indicador", value_name="Valor")
        st.plotly_chart(px.line(evol_long, x="DIA", y="Valor", color="Indicador", title="Evolução de pagamentos e receita comissionável"), use_container_width=True)
    if "FAIXA_ATRASO" in pagamentos_f.columns:
        faixa = pagamentos_f.groupby("FAIXA_ATRASO", as_index=False).agg(VALOR_PAGAMENTO=("VALOR_PAGAMENTO", "sum"), HO_COMISSIONAVEL=("HO_COMISSIONAVEL", "sum"))
        faixa_long = faixa.melt(id_vars=["FAIXA_ATRASO"], value_vars=["VALOR_PAGAMENTO", "HO_COMISSIONAVEL"], var_name="Indicador", value_name="Valor")
        st.plotly_chart(px.bar(faixa_long, x="FAIXA_ATRASO", y="Valor", color="Indicador", barmode="group", title="Pagamentos por faixa"), use_container_width=True)

    st.subheader("Pagamentos classificados")
    st.dataframe(format_display_df(pagamentos_f), use_container_width=True)


def page_operadores():
    st.title("Produtividade por Operador")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos, acionamentos)
    base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos, acionamentos, filtros_datas)
    if acionamentos.empty:
        st.info("Sem acionamentos importados.")
        return
    op = operadores(acionamentos)
    st.dataframe(format_display_df(op), use_container_width=True)
    if not op.empty:
        top = op.head(20)
        top_long = top.melt(id_vars=["RESPONSAVEL"], value_vars=[c for c in OCC_TYPES if c in top.columns], var_name="Etapa", value_name="Quantidade")
        st.plotly_chart(px.bar(top_long, x="RESPONSAVEL", y="Quantidade", color="Etapa", barmode="group", title="Top 20 operadores"), use_container_width=True)


def page_regua_receita():
    st.title("Régua de Receita e Comissionamento")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos, acionamentos)
    base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos, acionamentos, filtros_datas)

    st.caption(
        "A régua considera somente pagamentos e acionamentos dentro do mês da base selecionada, "
        "posteriores à entrada do ID_FIN na carteira. A receita comissionável depende de acordo BL "
        "antes do pagamento ou acionamento prévio em caso de pagamento indireto."
    )

    if pagamentos.empty:
        st.info("Não há pagamentos elegíveis para a régua selecionada.")
        return

    rk = receita_kpis(pagamentos)
    c = st.columns(6)
    c[0].metric("Pagamentos elegíveis", f"{rk['pagamentos_total']:,}".replace(",", "."))
    c[1].metric("Pagamentos comissionáveis", f"{rk['pagamentos_comissionaveis']:,}".replace(",", "."))
    c[2].metric("Valor pago elegível", format_currency(rk["valor_pago_total"]))
    c[3].metric("Saldo VPL total pago", format_currency(rk["saldo_vpl_pago_total"]))
    c[4].metric("Valor com acionamento", format_currency(rk["valor_pago_com_acionamento"]))
    c[5].metric("Valor sem acionamento", format_currency(rk["valor_pago_sem_acionamento"]))

    c = st.columns(6)
    c[0].metric("Valor comissionável", format_currency(rk["valor_pago_comissionavel"]))
    c[1].metric("H.O. comissionável", format_currency(rk["ho_comissionavel"]))
    c[2].metric("H.O. fora da régua", format_currency(rk["ho_nao_comissionavel"]))
    c[3].metric("Saldo VPL com acionamento", format_currency(rk["saldo_vpl_pago_com_acionamento"]))
    c[4].metric("Saldo VPL sem acionamento", format_currency(rk["saldo_vpl_pago_sem_acionamento"]))
    c[5].metric("Contratos pagos", f"{rk['contratos_pagos']:,}".replace(",", "."))

    tab1, tab2, tab3, tab4 = st.tabs(["Por tipo", "Por célula", "Por faixa", "Detalhe"] )
    with tab1:
        df = receita_by(pagamentos, "TIPO_RECEITA")
        st.dataframe(format_display_df(df), use_container_width=True)
        if not df.empty:
            df_long = df.melt(id_vars=["TIPO_RECEITA"], value_vars=["HO", "HO_COMISSIONAVEL", "HO_NAO_COMISSIONAVEL"], var_name="Indicador", value_name="Valor")
            st.plotly_chart(px.bar(df_long, x="TIPO_RECEITA", y="Valor", color="Indicador", barmode="group", title="H.O. por tipo de receita"), use_container_width=True)
    with tab2:
        col = "CELULA_VISAO" if "CELULA_VISAO" in pagamentos.columns else "BASE_CELULA_VISAO" if "BASE_CELULA_VISAO" in pagamentos.columns else "CELULA" if "CELULA" in pagamentos.columns else "BASE_CELULA" if "BASE_CELULA" in pagamentos.columns else None
        df = receita_by(pagamentos, col) if col else pd.DataFrame()
        if not df.empty and col != "CELULA":
            df = df.rename(columns={col: "CELULA"})
        st.dataframe(format_display_df(df), use_container_width=True)
    with tab3:
        col = "FAIXA_ATRASO" if "FAIXA_ATRASO" in pagamentos.columns else "BASE_FAIXA_ATRASO" if "BASE_FAIXA_ATRASO" in pagamentos.columns else None
        df = receita_by(pagamentos, col) if col else pd.DataFrame()
        st.dataframe(format_display_df(df), use_container_width=True)
    with tab4:
        st.dataframe(format_display_df(pagamentos), use_container_width=True)
        st.download_button(
            "Baixar régua de receita em Excel",
            to_excel_bytes({"Regua_Receita": pagamentos, "Resumo_Tipo": receita_by(pagamentos, "TIPO_RECEITA")}),
            "regua_receita_sol.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def page_novas_entradas():
    st.title("Novas Entradas e Evolução da Base")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    if base.empty:
        st.info("Importe pelo menos uma base diária.")
        return

    base_tmp = base.copy()
    base_tmp["DATA_BASE"] = pd.to_datetime(base_tmp["DATA_BASE"], errors="coerce")
    datas = sorted(base_tmp["DATA_BASE"].dropna().dt.date.unique(), reverse=True)
    meses = sorted({pd.Timestamp(d).to_period("M") for d in datas}, reverse=True)
    mes = st.selectbox("Mês de análise", meses, index=0, format_func=_period_label)
    datas_mes = sorted([d for d in datas if pd.Timestamp(d).to_period("M") == mes], reverse=True)
    data_base = st.selectbox(
        "Data atual para comparação",
        datas_mes,
        index=0,
        format_func=lambda d: pd.Timestamp(d).strftime("%d/%m/%Y"),
    )

    new, removed, kept, changes = novas_entradas(base, data_base)
    ev = base_evolution_summary(base, selected_month=mes)
    linha_atual = ev[ev["DATA_BASE"] == data_base].iloc[0].to_dict() if not ev.empty and (ev["DATA_BASE"] == data_base).any() else {}

    qtd_reentradas = int((new.get("STATUS_MOVIMENTO", pd.Series(dtype=str)) == "REENTRADA").sum()) if not new.empty else 0
    qtd_novas = int((new.get("STATUS_MOVIMENTO", pd.Series(dtype=str)) == "NOVA_ENTRADA").sum()) if not new.empty else 0
    qtd_devolvidos = int((removed.get("STATUS_MOVIMENTO", pd.Series(dtype=str)) == "DEVOLVIDO").sum()) if not removed.empty else 0

    c = st.columns(5)
    c[0].metric("Qtd. dia anterior", _format_number_br(linha_atual.get("QTD_DIA_ANTERIOR", 0)))
    c[1].metric("Qtd. dia atual", _format_number_br(linha_atual.get("QTD_DIA_ATUAL", 0)))
    c[2].metric("Entradas no dia", _format_number_br(linha_atual.get("ENTRADAS_ID_FIN", 0)))
    c[3].metric("Saídas no dia", _format_number_br(linha_atual.get("SAIDAS_ID_FIN", 0)))
    c[4].metric("Saldo VPL atual", format_currency(linha_atual.get("VPL_DIA_ATUAL", 0)))

    c = st.columns(6)
    c[0].metric("Novos ID_FIN", _format_number_br(qtd_novas))
    c[1].metric("Reentradas", _format_number_br(qtd_reentradas))
    c[2].metric("Devolvidos", _format_number_br(qtd_devolvidos))
    c[3].metric("Removidos total", _format_number_br(removed["ID_FIN"].nunique() if not removed.empty and "ID_FIN" in removed.columns else 0))
    c[4].metric("Mantidos", _format_number_br(kept["ID_FIN"].nunique() if not kept.empty and "ID_FIN" in kept.columns else 0))
    c[5].metric("Mudança de faixa", _format_number_br(len(changes) if not changes.empty else 0))

    reentradas = new[new.get("STATUS_MOVIMENTO", pd.Series(dtype=str)).eq("REENTRADA")].copy() if not new.empty else pd.DataFrame()
    devolvidos = removed[removed.get("STATUS_MOVIMENTO", pd.Series(dtype=str)).eq("DEVOLVIDO")].copy() if not removed.empty else pd.DataFrame()

    tabs = st.tabs(["Entradas", "Reentradas", "Removidos/Devolvidos", "Mantidos", "Mudança de faixa", "Evolução diária"])
    tabs[0].dataframe(format_display_df(new), use_container_width=True)
    tabs[1].dataframe(format_display_df(reentradas), use_container_width=True)
    tabs[2].dataframe(format_display_df(removed), use_container_width=True)
    tabs[3].dataframe(format_display_df(kept), use_container_width=True)
    tabs[4].dataframe(format_display_df(changes), use_container_width=True)
    with tabs[5]:
        st.caption("Resumo dia a dia do mês selecionado, comparando cada data com a importação imediatamente anterior. A coluna BASE_ATUAL indica a última base enviada.")
        if ev.empty:
            st.info("Sem evolução para o mês selecionado.")
            return
        st.dataframe(format_display_df(ev), use_container_width=True)

        g1, g2 = st.columns(2)
        with g1:
            st.plotly_chart(px.line(ev, x="DATA_BASE", y=["QTD_DIA_ANTERIOR", "QTD_DIA_ATUAL"], title="Contratos: dia anterior x dia atual"), use_container_width=True)
        with g2:
            fluxo_cols = [c for c in ["NOVAS_ENTRADAS_ID_FIN", "REENTRADAS_ID_FIN", "DEVOLVIDOS_ID_FIN", "SAIDAS_TEMPORARIAS_ID_FIN"] if c in ev.columns]
            ev_fluxo = ev.melt(id_vars=["DATA_BASE"], value_vars=fluxo_cols, var_name="Movimento", value_name="Quantidade")
            st.plotly_chart(px.bar(ev_fluxo, x="DATA_BASE", y="Quantidade", color="Movimento", barmode="group", title="Entradas, reentradas e devoluções por dia"), use_container_width=True)
        st.plotly_chart(px.line(ev, x="DATA_BASE", y="VPL_DIA_ATUAL", title="Saldo VPL dia a dia"), use_container_width=True)


def page_cpf_unico():
    st.title("Base CPF/CNPJ Único para CRM")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos, acionamentos)
    base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos, acionamentos, filtros_datas)
    base_enriched = enrich_base_with_activity(base_dia, pagamentos, acionamentos)
    df = cpf_unico(base_enriched, acionamentos)
    if df.empty:
        st.info("Sem dados para gerar CPF único.")
        return
    st.dataframe(format_display_df(df), use_container_width=True)
    st.download_button("Baixar CPF único em Excel", to_excel_bytes({"CPF_Unico_CRM": df}), "cpf_unico_crm.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def page_parametros():
    st.title("Parâmetros e DePara")
    tab1, tab2, tab3, tab4 = st.tabs(["Ocorrências", "Atraso / H.O.", "Operadores", "Metas"])
    tables = [TABLES["depara_ocorrencias"], TABLES["depara_atraso"], TABLES["depara_operadores"], TABLES["metas"]]
    labels = ["depara_ocorrencias", "depara_atraso", "depara_operadores", "metas"]
    for tab, table, label in zip([tab1, tab2, tab3, tab4], tables, labels):
        with tab:
            df = load_table(table)
            st.dataframe(format_display_df(df), use_container_width=True)
            uploaded = st.file_uploader(f"Atualizar {label}", type=["xlsx", "csv"], key=label)
            if uploaded and st.button(f"Importar {label}", key=f"btn_{label}"):
                raw = read_table(uploaded)
                if label == "depara_ocorrencias":
                    raw = normalize_depara_ocorrencias(raw)
                elif label == "depara_atraso":
                    raw = normalize_depara_atraso(raw)
                elif label == "metas":
                    raw = transform_metas(raw)
                save_dataframe(raw, table, uploaded.name, mode="replace_all")
                st.success("Parâmetro atualizado. Recarregue a página para ver o resultado.")
            if st.button(f"Limpar {label}", key=f"clear_{label}"):
                clear_table(table)
                st.warning("Tabela limpa. Recarregue a página.")


def page_exportacoes():
    st.title("Exportações")
    base, pagamentos, acionamentos, depara_atraso, depara_ocorrencias = load_all()
    filtros_datas = sidebar_filters(base, pagamentos, acionamentos)
    base_dia, pagamentos, acionamentos = apply_basic_filters(base, pagamentos, acionamentos, filtros_datas)
    base_enriched = enrich_base_with_activity(base_dia, pagamentos, acionamentos)
    sheets = {
        "Base_Tratada": base_enriched,
        "Pagamentos_Regua_Receita": pagamentos,
        "Resumo_Receita": receita_by(pagamentos, "TIPO_RECEITA"),
        "Acionamentos_Validos": acionamentos,
        "Resumo_Celula": funil_by(base_enriched, "CELULA_VISAO" if "CELULA_VISAO" in base_enriched.columns else "CELULA"),
        "Resumo_Faixa": funil_by(base_enriched, "FAIXA_ATRASO"),
        "Operadores": operadores(acionamentos),
        "CPF_Unico_CRM": cpf_unico(base_enriched, acionamentos),
    }
    st.write("O arquivo exportado terá as principais visões já tratadas.")
    st.download_button(
        "Baixar relatório Excel completo",
        data=to_excel_bytes(sheets),
        file_name=f"relatorio_sol_{_fmt_filter_date(filtros_datas.get('base_clientes')).replace('/', '-')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.subheader("Logs de importação")
    st.dataframe(format_display_df(load_table("logs_importacao")), use_container_width=True)
    st.subheader("Inconsistências")
    st.dataframe(format_display_df(load_table("inconsistencias_importacao")), use_container_width=True)


def main():
    st.sidebar.title("SOL / Solfácil")
    st.sidebar.caption(f"Banco local: {DB_PATH}")
    page = st.sidebar.radio(
        "Menu",
        [
            "Dashboard Geral",
            "Importar arquivos",
            "Funil de Acionamento",
            "Pagamentos e H.O.",
            "Régua de Receita",
            "Produtividade por Operador",
            "Novas Entradas",
            "CPF Único CRM",
            "Parâmetros / DePara",
            "Exportações e Logs",
        ],
    )
    if page == "Dashboard Geral":
        page_dashboard()
    elif page == "Importar arquivos":
        page_importar()
    elif page == "Funil de Acionamento":
        page_funil()
    elif page == "Pagamentos e H.O.":
        page_pagamentos()
    elif page == "Régua de Receita":
        page_regua_receita()
    elif page == "Produtividade por Operador":
        page_operadores()
    elif page == "Novas Entradas":
        page_novas_entradas()
    elif page == "CPF Único CRM":
        page_cpf_unico()
    elif page == "Parâmetros / DePara":
        page_parametros()
    elif page == "Exportações e Logs":
        page_exportacoes()


if __name__ == "__main__":
    main()
