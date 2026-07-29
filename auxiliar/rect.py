import fitz

doc = fitz.open("nota.pdf")
pagina = doc[0]

for bloco in pagina.get_text("blocks"):
    x0, y0, x1, y1, texto, _, _ = bloco
    
    if any(char.isdigit() for char in texto):
        retangulo = fitz.Rect(x0, y0, x1, y1)
        
        # Desenha o retângulo vermelho
        annot = pagina.add_rect_annot(retangulo)
        annot.set_colors(stroke=(1, 0, 0))
        annot.update()
        
        # AJUSTE: Move o texto informativo bem para a esquerda do número (x0 - 70) 
        # para não encavalar com os textos do centro do PDF
        texto_limpo = texto.replace('\n', ' ').strip()
        pagina.insert_text((x0 - 75, y0 + 8), f"x0:{int(x0)} y0:{int(y0)}", fontsize=7, color=(0, 0, 1))

doc.save("gabarito_coordenadas.pdf")
print("PDF de gabarito gerado com sucesso!")

