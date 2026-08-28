from pymupdf import Rect

areaDict_btg_day = { 
	'nrNota': Rect(448, 57, 491, 67),
	'liquido': Rect(463, 717, 566, 727),
	'semiLiquido': Rect(354, 718, 457, 727),
	'irrf': Rect(135, 676, 238, 685),
	'data': Rect(526, 57, 567, 65),
	'cpf': Rect(469, 138, 535, 146),
	'nCliente': Rect(485, 157, 528, 166),
    'continua': Rect(490, 621, 556, 637),
}

areaDict_xp_day = { 
	'nrNota': Rect(446, 59, 482, 67),
	'liquido': Rect(458, 709, 559, 717),
	'semiLiquido': Rect(352, 709, 453, 717),
	'irrf': Rect(140, 667, 241, 675),
	'data': Rect(518, 59, 560, 69),
	'cpf': Rect(473, 135, 527, 144),
	'nCliente': Rect(479, 152, 516, 160),
}

brokerName_day = { 
	'coords_xp': Rect(33, 77, 277, 93),
	'coords_btg': Rect(26, 75, 119, 84),
}

brokerName_swing = {
        'coords_xp': Rect(123, 75, 198, 83), 
        'coords_btg': Rect(123, 65, 195, 74), 
}

# pdfplumber / PyMuPDF mixed configuration
areaDict_xp_swing = {
     'nrNota': Rect(428, 58, 474, 67),
     'data': Rect(515, 58, 565, 67),
     'cpf': Rect(428, 148, 559, 158),
     'nCliente': Rect(430, 164, 520, 176),
     'tableAreaOperacoes': (30, 239, 559, 432), 
     'headerArea': Rect(30, 240, 559, 249),
     'tableAreaResumo': Rect(34, 447, 297, 530),
     'tableClearing': Rect(299, 456, 565, 489),
     'tableBolsa': Rect(299, 497, 565, 538),
     'tableCustos': Rect(299, 538, 565, 633),
     'irrf_day': Rect(31, 581, 135, 593),
     'columns': {
         'operacaoTipo': 'C/V',
         'nomeAtivo': 'Especificação do título',
         'quantidade': 'Quantidade',
         'precoAjuste': 'Preço / Ajuste',
         'precoOperacao': 'Valor Operação / Ajuste',
         'obs': 'Obs. (*)',
     },
     'headers': ['Q', 'Negociação', 'C/V', 'Tipo mercado', 'Prazo', 'Especificação do título', 'Obs. (*)', 'Quantidade', 'Preço / Ajuste', 'Valor Operação / Ajuste', 'D/C'],
}

areaDict_btg_swing = { 
     'nrNota': Rect(402, 62, 456, 71),
     'data': Rect(511, 62, 567, 72),
     'cpf': Rect(423, 149, 567, 159),
     'nCliente': Rect(423, 167, 516, 179),
     'continua': Rect(490, 621, 556, 637),
     'tableAreaOperacoes': (23, 240, 569, 454), 
     'headerArea': Rect(22, 242, 566, 251),
     'tableAreaResumo': Rect(26, 507, 295, 596),
     'tableClearing': Rect(298, 515, 567, 551),
     'tableBolsa': Rect(297, 561, 566, 622),
     'tableCustos': Rect(297, 633, 567, 716),
     'irrf_day': Rect(31, 581, 135, 593), # NAO VERIFICADO
     'columns': {
         'operacaoTipo': 'C/V',
         'nomeAtivo': 'Especificação do título',
         'quantidade': 'Quantidade',
         'precoAjuste': 'Preço / Ajuste',
         'precoOperacao': 'Valor Operação / Ajuste',
         'obs': 'Obs. (*)',
     },
     'headers': ['Q', 'Negociação', 'C/V', 'Tipo mercado', 'Prazo', 'Especificação do título', 'Obs. (*)', 'Quantidade', 'Preço / Ajuste', 'Valor Operação / Ajuste', 'D/C'],
}

areaDict_day_or_swing = {
        'swing_btg': Rect(105, 48, 198, 62), 
        'swing_xp': Rect(106, 37, 217, 51),
        'day_btg': Rect(214, 47, 349, 64),
        'day_xp': Rect(188, 48, 302, 61),
}
