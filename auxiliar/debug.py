import pdfplumber
from PIL import Image, ImageTk
import tkinter as tk

# Altere para o nome exato do seu arquivo PDF
PDF_PATH = "nota_1.pdf"

# Configuração de tamanho para caber em qualquer tela
ALTURA_MAXIMA = 600

with pdfplumber.open(PDF_PATH) as pdf:
    primeira_pagina = pdf.pages[0]
    pdf_width = primeira_pagina.width
    pdf_height = primeira_pagina.height
    
    # Gera a imagem inicial em boa qualidade
    pag_imagem = primeira_pagina.to_image(resolution=150)
    img_original = pag_imagem.original

# Calcula a nova largura mantendo a proporção original do PDF
proporcao = ALTURA_MAXIMA / img_original.height
nova_largura = int(img_original.width * proporcao)

# Redimensiona a imagem para o tamanho seguro
img_pil = img_original.resize((nova_largura, ALTURA_MAXIMA), Image.Resampling.LANCZOS)

# Configura a interface gráfica
root = tk.Tk()
root.title("Visualizador Compacto de Coordenadas PDF")

img_tk = ImageTk.PhotoImage(img_pil)
canvas = tk.Canvas(root, width=img_pil.width, height=img_pil.height)
canvas.pack()
canvas.create_image(0, 0, anchor="nw", image=img_tk)

def ao_clicar(event):
    # Converte o clique na tela diretamente para a escala de pontos do PDF original
    escala_x = pdf_width / img_pil.width
    escala_y = pdf_height / img_pil.height
    
    pdf_x = event.x * escala_x
    pdf_y = event.y * escala_y
    
    print(f"📍 Clique detectado -> X: {pdf_x:.2f} | Y: {pdf_y:.2f}")

canvas.bind("<Button-1>", ao_clicar)

print("👉 Imagem compactada! Agora ela deve caber inteira na sua tela.")
root.mainloop()
