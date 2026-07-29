from datetime import datetime
from decimal import Decimal

def clean_int(text): 
    text = str(text or "0").strip()
    text = text.replace(".", "")
    try: 
        return int(text)
    except ValueError: 
        return 0

def clean_decimal(text): 
    text = str(text or "0").strip()
    text = text.split()[-1]
    text = text.replace(".", "")
    text = text.replace(",", ".")
    try: 
        return Decimal(text)
    except Exception: 
        return Decimal("0")

def text_to_numbers(text): 
    text = str(text).strip()

    neg = "D" in text.upper()
    text = text.split("|")[0].strip()
    text = text.replace("\n", "")
    text = text.replace(".", "")
    text = text.replace(",", ".")

    try: 
        num = float(text)
    except ValueError: 
        return 0.0
    if neg: 
        num = -abs(num)
    return num

import re

def date_to_datetime(text): 
    text = str(text or "").strip()
    match = re.search(r'\b\d{2}/\d{2}/\d{4}\b', text)
    if match:
        try:
            return datetime.strptime(match.group(0), "%d/%m/%Y")
        except Exception:
            pass
    return datetime.today()

def placeholder(text):
    return text

def clean_cpf(text):
    text = str(text or "")
    match = re.search(r'\d{3}\.\d{3}\.\d{3}-\d{2}', text)
    if match:
        return match.group(0)
    return text.strip()

def clean_operacao_tipo(text):
    text = str(text or "").strip().upper()
    if text.startswith('C'):
        return 'C'
    if text.startswith('V'):
        return 'V'
    if 'C' in text:
        return 'C'
    if 'V' in text:
        return 'V'
    return text

def clean_n_cliente(text):
    text = str(text or "").strip()
    matches = re.findall(r'\d{4,}', text)
    if matches:
        return str(int(matches[0]))

def clean_nome_ativo(text):
    text = str(text or "").strip()
    text = re.sub(r'[\s@#*]+$', '', text).strip()
    return text

cleanup_dict = { 
                 "liquido": text_to_numbers,
                 "semiLiquido": text_to_numbers,
                 "irrf": text_to_numbers,
                 "data": date_to_datetime,
                 "cpf": clean_cpf,
                 "nCliente": clean_n_cliente,
                 "operacaoTipo": clean_operacao_tipo,
                 "nomeAtivo": clean_nome_ativo,
                 "quantidade": clean_int,
                 "precoAjuste": clean_decimal,
                 "precoOperacao": clean_decimal,
                 "nrNota": clean_int,
                 }

