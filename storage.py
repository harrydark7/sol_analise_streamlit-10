from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .config import DATA_DIR, DB_PATH, TABLES, TEMPLATE_DIR


OPERATIONAL_TABLES = {TABLES["base"], TABLES["pagamentos"], TABLES["acionamentos"], TABLES.get("base_congelada", "base_congelada")}


def get_conn(db_path: Path = DB_PATH):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def _table_exists_conn(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    if not _table_exists_conn(conn, table_name):
        return []
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [r[1] for r in rows]


def _migrate_import_date_columns(conn: sqlite3.Connection):
    """Garante DATA_IMPORTACAO nas tabelas operacionais e preenche bases antigas."""
    for table_name in OPERATIONAL_TABLES:
        if not _table_exists_conn(conn, table_name):
            continue

        cols = _table_columns(conn, table_name)
        if "DATA_IMPORTACAO" not in cols:
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN DATA_IMPORTACAO TEXT')
            cols.append("DATA_IMPORTACAO")

        # Base de clientes: a data da importação/referência deve acompanhar DATA_BASE quando existir.
        if table_name == TABLES["base"] and "DATA_BASE" in cols:
            fallback_expr = "substr(DATA_BASE, 1, 10)"
        elif "IMPORT_BATCH_ID" in cols:
            fallback_expr = (
                f'(SELECT COALESCE(NULLIF(l.data_base, \'\'), substr(l.imported_at, 1, 10)) '
                f'FROM logs_importacao l WHERE l.import_batch_id = "{table_name}".IMPORT_BATCH_ID)'
            )
        elif "IMPORTED_AT" in cols:
            fallback_expr = "substr(IMPORTED_AT, 1, 10)"
        else:
            fallback_expr = "date('now')"

        imported_at_expr = "substr(IMPORTED_AT, 1, 10)" if "IMPORTED_AT" in cols else "date('now')"
        conn.execute(
            f'''
            UPDATE "{table_name}"
               SET DATA_IMPORTACAO = COALESCE(NULLIF(DATA_IMPORTACAO, ''), {fallback_expr}, {imported_at_expr}, date('now'))
             WHERE DATA_IMPORTACAO IS NULL OR DATA_IMPORTACAO = ''
            '''
        )


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs_importacao (
                import_batch_id TEXT PRIMARY KEY,
                tabela TEXT,
                arquivo TEXT,
                linhas INTEGER,
                status TEXT,
                usuario TEXT,
                data_base TEXT,
                imported_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inconsistencias_importacao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_batch_id TEXT,
                severidade TEXT,
                campo TEXT,
                problema TEXT,
                created_at TEXT
            )
            """
        )
        _migrate_import_date_columns(conn)
        conn.commit()


def table_exists(table_name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return cur.fetchone() is not None


def list_tables() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def load_table(table_name: str) -> pd.DataFrame:
    init_db()
    if not table_exists(table_name):
        return pd.DataFrame()
    with get_conn() as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)


def _prepare_to_sql(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


def _format_import_date(value: str | date | datetime | None) -> str:
    if value is None or str(value).strip() == "":
        return date.today().isoformat()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return date.today().isoformat()
    return parsed.date().isoformat()


def save_dataframe(
    df: pd.DataFrame,
    table_name: str,
    filename: str = "",
    mode: str = "append_dedup",
    key_cols: list[str] | None = None,
    data_base: str | None = None,
    usuario: str = "streamlit",
) -> str:
    init_db()
    batch_id = str(uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    import_date = _format_import_date(data_base)

    to_save = df.copy()
    if table_name in OPERATIONAL_TABLES:
        # DATA_IMPORTACAO é a data de referência do arquivo importado.
        # Ela permite filtrar separadamente Base de Clientes, Pagamentos e Acionamentos.
        to_save["DATA_IMPORTACAO"] = import_date
    to_save["IMPORT_BATCH_ID"] = batch_id
    to_save["IMPORTED_AT"] = now
    to_save = _prepare_to_sql(to_save)

    with get_conn() as conn:
        if mode == "replace_all" or not table_exists(table_name):
            to_save.to_sql(table_name, conn, if_exists="replace", index=False)
        elif mode == "replace_same_data_base" and data_base:
            existing = load_table(table_name)
            date_col = "DATA_BASE" if "DATA_BASE" in existing.columns else "DATA_IMPORTACAO" if "DATA_IMPORTACAO" in existing.columns else None
            if not existing.empty and date_col:
                existing["__DATA_REF__"] = pd.to_datetime(existing[date_col], errors="coerce").dt.date.astype(str)
                existing = existing[existing["__DATA_REF__"] != import_date].drop(columns=["__DATA_REF__"])
                combined = pd.concat([existing, to_save], ignore_index=True, sort=False)
                combined.to_sql(table_name, conn, if_exists="replace", index=False)
            else:
                to_save.to_sql(table_name, conn, if_exists="append", index=False)
        else:
            existing = load_table(table_name)
            combined = pd.concat([existing, to_save], ignore_index=True, sort=False)
            if key_cols:
                real_keys = [c for c in key_cols if c in combined.columns]
                if real_keys:
                    combined = combined.drop_duplicates(subset=real_keys, keep="last")
            combined.to_sql(table_name, conn, if_exists="replace", index=False)

        conn.execute(
            "INSERT OR REPLACE INTO logs_importacao VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, table_name, filename, len(df), "OK", usuario, import_date, now),
        )
        _migrate_import_date_columns(conn)
        conn.commit()
    return batch_id


def save_issues(issues: pd.DataFrame, batch_id: str):
    if issues is None or issues.empty:
        return
    data = issues.copy()
    data["import_batch_id"] = batch_id
    data["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        data[["import_batch_id", "severidade", "campo", "problema", "created_at"]].to_sql(
            "inconsistencias_importacao", conn, if_exists="append", index=False
        )


def load_defaults_if_empty():
    """Carrega DeParas padrão da pasta templates quando o banco ainda não possui esses parâmetros."""
    for key, table in [("depara_ocorrencias", TABLES["depara_ocorrencias"]), ("depara_atraso", TABLES["depara_atraso"]), ("depara_operadores", TABLES["depara_operadores"]), ("metas", TABLES["metas"] )]:
        if table_exists(table) and not load_table(table).empty:
            continue
        csv_path = TEMPLATE_DIR / f"{key}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path)
            save_dataframe(df, table, filename=csv_path.name, mode="replace_all")


def clear_table(table_name: str):
    with get_conn() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        conn.commit()
