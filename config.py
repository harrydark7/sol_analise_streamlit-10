from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
TEMPLATE_DIR = DATA_DIR / "templates"
DB_PATH = DATA_DIR / "solfacil.db"

APP_TITLE = "SOL / Solfácil | Análise de Carteira"

BASE_REQUIRED = ["ID_FIN", "CPF_CNPJ"]
PAGAMENTOS_REQUIRED = ["ID_FIN", "DATA_VENCIMENTO", "DATA_PAGAMENTO_BOLETO", "VALOR_PAGAMENTO"]
ACIONAMENTOS_REQUIRED = ["ID_FIN", "FINALIZACAO", "DATA_ACIONAMENTO"]

TABLES = {
    "base": "base_diaria",
    "pagamentos": "pagamentos",
    "acionamentos": "acionamentos",
    "base_congelada": "base_congelada",
    "depara_ocorrencias": "depara_ocorrencias",
    "depara_atraso": "depara_atraso",
    "depara_operadores": "depara_operadores",
    "metas": "metas",
}

DEFAULT_COLUMNS = {
    "base": [
        "ID_FIN", "FUNDO", "TIPO_CLIENTE", "CCB", "DIAS_ATRASO", "CARTEIRA", "VALOR_BRUTO", "VPL",
        "SALDO_EM_ATRASO", "PARCELAS_EM_ATRASO", "PARCELAS_PAGAS", "ETAPA_ATUAL", "CARENCIA",
        "STATUS_COBRANCA", "NOVO", "COD_PRODUT", "CPF_CNPJ", "SITU_COB", "ACORDO",
        "VPL_CONVERTIDO", "DATA_BASE"
    ],
    "pagamentos": [
        "CCB", "ID_FIN", "FUNDO", "PARCELA", "DATA_VENCIMENTO", "DATA_PAGAMENTO_BOLETO", "DIAS_ATRASO",
        "VALOR_PAGAMENTO", "CARTEIRA"
    ],
    "acionamentos": [
        "COD_HISTO", "ID_FIN", "FINALIZACAO", "DATA_ACIONAMENTO", "DATA_AGENDADA", "MOTIVO_ATRASO",
        "CARTEIRA_DISTRIBUIDA", "RESPONSAVEL", "TELEFONE_ACIONADO", "TIPO_ACIONAMENTO",
        "CANAL_ACIONAMENTO", "COMENTARIO_ACIONAMENTO"
    ],
}
