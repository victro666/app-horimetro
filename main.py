import os
import sqlite3
import urllib.parse
from datetime import datetime
import flet as ft
from fpdf import FPDF


# =====================================================================
# 1. CAMADA DE PERSISTÊNCIA E CAMINHOS ANDROID (SQLITE LOCAL)
# =====================================================================
def obter_diretorio_dados():
    """Retorna um diretório com permissão de escrita garantida no Android e no PC."""
    caminho = os.environ.get("FLET_APP_STORAGE_DATA")
    if not caminho:
        caminho = os.path.join(os.path.expanduser("~"), "horimetro_mvp_data")
    os.makedirs(caminho, exist_ok=True)
    return caminho


DB_PATH = os.path.join(obter_diretorio_dados(), "manutencao_frota.db")


def conectar_bd():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def inicializar_banco():
    conn = conectar_bd()
    cursor = conn.cursor()

    # Tabela 1: Tratores / Máquinas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Tratores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            placa TEXT NOT NULL,
            horimetro_inicial REAL NOT NULL
        )
    """)

    # Tabela 2: Planos de Manutenção (Regras por Horímetro)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Planos_Manutencao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_trator INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            tipo TEXT NOT NULL,
            intervalo_horas REAL NOT NULL,
            margem_aviso_horas REAL NOT NULL,
            horimetro_ultima_manutencao REAL NOT NULL,
            custo_previsto REAL DEFAULT 0.0,
            FOREIGN KEY (id_trator) REFERENCES Tratores(id)
        )
    """)

    # Tabela 3: Apontamentos de Horas Trabalhadas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Apontamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_trator INTEGER NOT NULL,
            horas_registradas REAL NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (id_trator) REFERENCES Tratores(id)
        )
    """)

    # Tabela 4: Histórico de Manutenções Realizadas (Controle de Custos)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Manutencoes_Realizadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_trator INTEGER NOT NULL,
            id_plano INTEGER,
            categoria TEXT NOT NULL,
            servico TEXT NOT NULL,
            horimetro_execucao REAL NOT NULL,
            custo_real REAL NOT NULL,
            data TEXT NOT NULL,
            FOREIGN KEY (id_trator) REFERENCES Tratores(id)
        )
    """)

    # Seed inicial para demonstração imediata ao cliente (se banco estiver vazio)
    cursor.execute("SELECT COUNT(*) FROM Tratores")
    if cursor.fetchone()[0] == 0:
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        # Máquina 1: Retroescavadeira
        cursor.execute(
            "INSERT INTO Tratores (nome, placa, horimetro_inicial) VALUES (?, ?, ?)",
            ("Retroescavadeira CAT 416E", "CAT-4160", 1000),
        )
        id_retro = cursor.lastrowid
        cursor.execute(
            "INSERT INTO Apontamentos (id_trator, horas_registradas, data) VALUES (?, ?, ?)",
            (id_retro, 1270, agora),
        )
        # Plano Atrasado (1000 + 250 = 1250h -> Atual 1270h = Passou 20h)
        cursor.execute(
            """INSERT INTO Planos_Manutencao 
            (id_trator, categoria, tipo, intervalo_horas, margem_aviso_horas, horimetro_ultima_manutencao, custo_previsto)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_retro, "Preventiva", "Troca de Óleo Motor e Filtro", 250, 30, 1000, 680.00),
        )

        # Máquina 2: Trator Agrícola
        cursor.execute(
            "INSERT INTO Tratores (nome, placa, horimetro_inicial) VALUES (?, ?, ?)",
            ("Trator John Deere 6110J", "JD-6110", 2000),
        )
        id_trator = cursor.lastrowid
        cursor.execute(
            "INSERT INTO Apontamentos (id_trator, horas_registradas, data) VALUES (?, ?, ?)",
            (id_trator, 2480, agora),
        )
        # Plano em Alerta/Margem (2000 + 500 = 2500h -> Atual 2480h = Faltam 20h, margem é 35h)
        cursor.execute(
            """INSERT INTO Planos_Manutencao 
            (id_trator, categoria, tipo, intervalo_horas, margem_aviso_horas, horimetro_ultima_manutencao, custo_previsto)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_trator, "Lubrificantes/Filtros", "Revisão Sistema Hidráulico 500h", 500, 35, 2000, 1450.00),
        )
        # Plano em Dia (2000 + 1000 = 3000h -> Atual 2480h = Faltam 520h)
        cursor.execute(
            """INSERT INTO Planos_Manutencao 
            (id_trator, categoria, tipo, intervalo_horas, margem_aviso_horas, horimetro_ultima_manutencao, custo_previsto)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_trator, "Preventiva", "Revisão Geral de Transmissão", 1000, 50, 2000, 3200.00),
        )

        # Histórico realizado de exemplo
        cursor.execute(
            """INSERT INTO Manutencoes_Realizadas
            (id_trator, id_plano, categoria, servico, horimetro_execucao, custo_real, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_retro, 1, "Preventiva", "Troca de Óleo Motor e Filtro", 1000, 650.00, "01/10/2026 09:00"),
        )

    conn.commit()
    conn.close()


# =====================================================================
# 2. MOTOR DE REGRAS DE HORÍMETRO E CONSULTAS
# =====================================================================
def obter_horimetro_atual(id_trator):
    conn = conectar_bd()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(horas_registradas) FROM Apontamentos WHERE id_trator = ?",
        (id_trator,),
    )
    res = cursor.fetchone()[0]
    if res is None:
        cursor.execute("SELECT horimetro_inicial FROM Tratores WHERE id = ?", (id_trator,))
        fila = cursor.fetchone()
        res = fila[0] if fila else 0.0
    conn.close()
    return float(res)


def calcular_painel_manutencoes(filtro_trator_id=None):
    """Cruza o último apontamento com os planos de manutenção e classifica o semáforo."""
    conn = conectar_bd()
    cursor = conn.cursor()

    query = """
        SELECT p.id, p.id_trator, t.nome, t.placa, p.categoria, p.tipo,
               p.intervalo_horas, p.margem_aviso_horas, p.horimetro_ultima_manutencao, p.custo_previsto
        FROM Planos_Manutencao p
        JOIN Tratores t ON p.id_trator = t.id
    """
    params = []
    if filtro_trator_id and filtro_trator_id != "TODOS":
        query += " WHERE t.id = ?"
        params.append(int(filtro_trator_id))

    cursor.execute(query, params)
    planos = cursor.fetchall()
    conn.close()

    resultado = []
    for p in planos:
        (
            id_plano,
            id_trator,
            nome_trator,
            placa,
            categoria,
            tipo,
            intervalo,
            margem,
            ultima_manut,
            custo_previsto,
        ) = p

        horimetro_atual = obter_horimetro_atual(id_trator)
        proxima_manutencao = ultima_manut + intervalo
        horas_restantes = proxima_manutencao - horimetro_atual

        if horas_restantes <= 0:
            status = "ATRASADA"
            prioridade = 1
            cor_fundo = "#FFEBEE"
            cor_borda = "#D32F2F"
            cor_texto = "#B71C1C"
            detalhe = f"ATRASADA (Passou {abs(horas_restantes):.0f}h do limite de {proxima_manutencao:.0f}h)"
        elif horas_restantes <= margem:
            status = "ALERTA"
            prioridade = 2
            cor_fundo = "#FFF8E1"
            cor_borda = "#F57C00"
            cor_texto = "#E65100"
            detalhe = f"EM ALERTA (Faltam apenas {horas_restantes:.0f}h - Revisar c/ {proxima_manutencao:.0f}h)"
        else:
            status = "EM DIA"
            prioridade = 3
            cor_fundo = "#E8F5E9"
            cor_borda = "#388E3C"
            cor_texto = "#1B5E20"
            detalhe = f"EM DIA (Faltam {horas_restantes:.0f}h - Próxima c/ {proxima_manutencao:.0f}h)"

        resultado.append(
            {
                "id_plano": id_plano,
                "id_trator": id_trator,
                "maquina": f"{nome_trator} ({placa})",
                "categoria": categoria,
                "tipo": tipo,
                "horimetro_atual": horimetro_atual,
                "proxima_manutencao": proxima_manutencao,
                "horas_restantes": horas_restantes,
                "custo_previsto": custo_previsto,
                "status": status,
                "prioridade": prioridade,
                "cor_fundo": cor_fundo,
                "cor_borda": cor_borda,
                "cor_texto": cor_texto,
                "detalhe": detalhe,
            }
        )

    resultado.sort(key=lambda x: (x["prioridade"], x["horas_restantes"]))
    return resultado


# =====================================================================
# 3. GERADOR DE RELATÓRIO EM PDF (RETRATO / PAISAGEM)
# =====================================================================
def texto_pdf_seguro(texto):
    """Remove caracteres incompatíveis com a fonte padrão Latin-1 do FPDF."""
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def gerar_relatorio_pdf(orientacao="P", filtro_trator_id="TODOS"):
    dados_agendadas = calcular_painel_manutencoes(filtro_trator_id)

    conn = conectar_bd()
    cursor = conn.cursor()
    query_realizadas = """
        SELECT r.data, t.nome, t.placa, r.categoria, r.servico, r.horimetro_execucao, r.custo_real
        FROM Manutencoes_Realizadas r
        JOIN Tratores t ON r.id_trator = t.id
    """
    params = []
    if filtro_trator_id and filtro_trator_id != "TODOS":
        query_realizadas += " WHERE t.id = ?"
        params.append(int(filtro_trator_id))
    query_realizadas += " ORDER BY r.id DESC"
    cursor.execute(query_realizadas, params)
    dados_realizadas = cursor.fetchall()
    conn.close()

    pdf = FPDF(orientation=orientacao, unit="mm", format="A4")
    pdf.add_page()

    # Cabeçalho
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, texto_pdf_seguro("RELATORIO DE GESTAO DE MANUTENCAO POR HORIMETRO"), ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        6,
        texto_pdf_seguro(f"Gerado em: {datetime.now().strftime('%d/%m/%Y as %H:%M')}"),
        ln=True,
        align="C",
    )
    pdf.ln(6)

    # Seção 1: Manutenções Planejadas / Semáforo
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, texto_pdf_seguro("1. Controle de Agendamentos e Alertas (Horimetro)"), ln=True)
    pdf.set_font("Helvetica", "B", 9)

    largura_maq = 65 if orientacao == "P" else 90
    largura_serv = 55 if orientacao == "P" else 85

    pdf.cell(largura_maq, 7, texto_pdf_seguro("Maquina (Placa)"), border=1)
    pdf.cell(largura_serv, 7, texto_pdf_seguro("Servico / Categoria"), border=1)
    pdf.cell(22, 7, texto_pdf_seguro("Atual (h)"), border=1, align="C")
    pdf.cell(22, 7, texto_pdf_seguro("Meta (h)"), border=1, align="C")
    pdf.cell(26, 7, texto_pdf_seguro("Custo Prev."), border=1, align="R", ln=True)

    pdf.set_font("Helvetica", "", 8)
    total_previsto = 0.0
    for item in dados_agendadas:
        total_previsto += item["custo_previsto"]
        prefixo = "[!] " if item["status"] != "EM DIA" else ""
        maq_txt = texto_pdf_seguro(f"{prefixo}{item['maquina']}")[:38]
        srv_txt = texto_pdf_seguro(f"{item['tipo']} ({item['status']})")[:35]

        pdf.cell(largura_maq, 7, maq_txt, border=1)
        pdf.cell(largura_serv, 7, srv_txt, border=1)
        pdf.cell(22, 7, f"{item['horimetro_atual']:.0f}h", border=1, align="C")
        pdf.cell(22, 7, f"{item['proxima_manutencao']:.0f}h", border=1, align="C")
        pdf.cell(26, 7, f"R$ {item['custo_previsto']:.2f}", border=1, align="R", ln=True)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(largura_maq + largura_serv + 44, 7, texto_pdf_seguro("Custo Total Previsto nas Proximas Manutencoes:"), border=1, align="R")
    pdf.cell(26, 7, f"R$ {total_previsto:.2f}", border=1, align="R", ln=True)
    pdf.ln(8)

    # Seção 2: Manutenções Realizadas e Custos
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, texto_pdf_seguro("2. Historico de Manutencoes Realizadas e Custos"), ln=True)
    pdf.set_font("Helvetica", "B", 9)

    pdf.cell(32, 7, texto_pdf_seguro("Data"), border=1)
    pdf.cell(largura_maq - 10, 7, texto_pdf_seguro("Maquina"), border=1)
    pdf.cell(largura_serv, 7, texto_pdf_seguro("Servico Executado"), border=1)
    pdf.cell(25, 7, texto_pdf_seguro("Horimetro"), border=1, align="C")
    pdf.cell(28, 7, texto_pdf_seguro("Custo Real"), border=1, align="R", ln=True)

    pdf.set_font("Helvetica", "", 8)
    total_realizado = 0.0
    for r in dados_realizadas:
        data_r, nome_t, placa_t, cat_r, serv_r, hor_r, custo_r = r
        total_realizado += custo_r
        pdf.cell(32, 7, texto_pdf_seguro(data_r), border=1)
        pdf.cell(largura_maq - 10, 7, texto_pdf_seguro(f"{nome_t} ({placa_t})")[:30], border=1)
        pdf.cell(largura_serv, 7, texto_pdf_seguro(f"{cat_r}: {serv_r}")[:34], border=1)
        pdf.cell(25, 7, f"{hor_r:.0f}h", border=1, align="C")
        pdf.cell(28, 7, f"R$ {custo_r:.2f}", border=1, align="R", ln=True)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(32 + (largura_maq - 10) + largura_serv + 25, 7, texto_pdf_seguro("Custo Total Realizado no Periodo:"), border=1, align="R")
    pdf.cell(28, 7, f"R$ {total_realizado:.2f}", border=1, align="R", ln=True)

    nome_arquivo = f"relatorio_frota_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    # Tenta gravar na pasta Downloads pública do Android se acessível; caso contrário, usa pasta segura do App
    pasta_downloads_android = "/storage/emulated/0/Download"
    caminho_final = os.path.join(obter_diretorio_dados(), nome_arquivo)

    if os.path.exists(pasta_downloads_android) and os.access(pasta_downloads_android, os.W_OK):
        try:
            caminho_downloads = os.path.join(pasta_downloads_android, nome_arquivo)
            pdf.output(caminho_downloads)
            return caminho_downloads
        except Exception:
            pass

    pdf.output(caminho_final)
    return caminho_final


# =====================================================================
# 4. INTERFACE GRÁFICA MOBILE (FLET)
# =====================================================================
def main(page: ft.Page):
    inicializar_banco()

    page.title = "Gestão de Frota por Horímetro"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.window_width = 410
    page.window_height = 780

    def mostrar_aviso(mensagem, cor="#2E7D32"):
        page.snack_bar = ft.SnackBar(content=ft.Text(mensagem, color="#FFFFFF"), bgcolor=cor)
        page.snack_bar.open = True
        page.update()

    def listar_tratores_opcoes():
        conn = conectar_bd()
        cursor = conn.cursor()
        cursor.execute("SELECT id, nome, placa FROM Tratores ORDER BY nome")
        linhas = cursor.fetchall()
        conn.close()
        return [ft.dropdown.Option(key=str(r[0]), text=f"{r[1]} ({r[2]})") for r in linhas]

    # -----------------------------------------------------------------
    # ABA 1: DASHBOARD DE ALERTAS (SEMÁFORO)
    # -----------------------------------------------------------------
    coluna_cards_alertas = ft.Column(spacing=10)
    txt_resumo_atrasadas = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color="#B71C1C")
    txt_resumo_alerta = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color="#E65100")
    txt_resumo_em_dia = ft.Text("0", size=22, weight=ft.FontWeight.BOLD, color="#1B5E20")

    def dar_baixa_manutencao(item):
        """Registra que a manutenção foi executada e atualiza o horímetro base do plano."""
        conn = conectar_bd()
        cursor = conn.cursor()
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")

        cursor.execute(
            """INSERT INTO Manutencoes_Realizadas 
            (id_trator, id_plano, categoria, servico, horimetro_execucao, custo_real, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item["id_trator"],
                item["id_plano"],
                item["categoria"],
                item["tipo"],
                item["horimetro_atual"],
                item["custo_previsto"],
                agora,
            ),
        )
        cursor.execute(
            "UPDATE Planos_Manutencao SET horimetro_ultima_manutencao = ? WHERE id = ?",
            (item["horimetro_atual"], item["id_plano"]),
        )
        conn.commit()
        conn.close()
        mostrar_aviso(f"Baixa registrada: {item['tipo']} ({item['horimetro_atual']:.0f}h)")
        atualizar_dashboard()

    def atualizar_dashboard():
        coluna_cards_alertas.controls.clear()
        itens = calcular_painel_manutencoes()

        qtd_atrasadas = sum(1 for x in itens if x["status"] == "ATRASADA")
        qtd_alerta = sum(1 for x in itens if x["status"] == "ALERTA")
        qtd_ok = sum(1 for x in itens if x["status"] == "EM DIA")

        txt_resumo_atrasadas.value = str(qtd_atrasadas)
        txt_resumo_alerta.value = str(qtd_alerta)
        txt_resumo_em_dia.value = str(qtd_ok)

        for item in itens:
            icone_status = "error" if item["status"] == "ATRASADA" else ("warning" if item["status"] == "ALERTA" else "check_circle")
            card = ft.Container(
                bgcolor=item["cor_fundo"],
                border=ft.border.all(2, item["cor_borda"]),
                border_radius=10,
                padding=12,
                content=ft.Column(
                    spacing=5,
                    controls=[
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        ft.Icon(name=icone_status, color=item["cor_borda"], size=22),
                                        ft.Text(item["maquina"], weight=ft.FontWeight.BOLD, size=15, color="#212121"),
                                    ],
                                ),
                                ft.Container(
                                    bgcolor=item["cor_borda"],
                                    padding=ft.padding.symmetric(horizontal=8, vertical=3),
                                    border_radius=5,
                                    content=ft.Text(item["status"], color="#FFFFFF", size=11, weight=ft.FontWeight.BOLD),
                                ),
                            ],
                        ),
                        ft.Text(f"{item['categoria']} • {item['tipo']}", size=14, weight=ft.FontWeight.W_600, color="#424242"),
                        ft.Text(item["detalhe"], size=13, color=item["cor_texto"], weight=ft.FontWeight.BOLD),
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Text(
                                    f"Horímetro Atual: {item['horimetro_atual']:.0f}h | Previsto: R$ {item['custo_previsto']:.2f}",
                                    size=12,
                                    color="#616161",
                                ),
                                ft.TextButton(
                                    text="Dar Baixa (Concluir)",
                                    icon="done_all",
                                    on_click=lambda e, i=item: dar_baixa_manutencao(i),
                                ),
                            ],
                        ),
                    ],
                ),
            )
            coluna_cards_alertas.controls.append(card)
        page.update()

    tela_dashboard = ft.Container(
        padding=15,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[
                ft.Text("Semáforo de Manutenção", size=20, weight=ft.FontWeight.BOLD),
                ft.Row(
                     spacing=8,
                    controls=[
                        ft.Container(
                            expand=1,
                            bgcolor="#FFEBEE",
                            border=ft.border.all(1, "#D32F2F"),
                            border_radius=8,
                            padding=10,
                            content=ft.Column(
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[txt_resumo_atrasadas, ft.Text("Atrasadas", size=12, color="#B71C1C")],
                            ),
                        ),
                        ft.Container(
                            expand=1,
                            bgcolor="#FFF8E1",
                            border=ft.border.all(1, "#F57C00"),
                            border_radius=8,
                            padding=10,
                            content=ft.Column(
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[txt_resumo_alerta, ft.Text("Na Margem", size=12, color="#E65100")],
                            ),
                        ),
                        ft.Container(
                            expand=1,
                            bgcolor="#E8F5E9",
                            border=ft.border.all(1, "#388E3C"),
                            border_radius=8,
                            padding=10,
                            content=ft.Column(
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[txt_resumo_em_dia, ft.Text("Em Dia", size=12, color="#1B5E20")],
                            ),
                        ),
                    ],
                ),
                ft.Divider(),
                coluna_cards_alertas,
            ],
        ),
    )

    # -----------------------------------------------------------------
    # ABA 2: NOVO APONTAMENTO DE HORÍMETRO (OPERADOR)
    # -----------------------------------------------------------------
    dd_trator_apontamento = ft.Dropdown(label="Selecione a Máquina / Trator")
    txt_horimetro_anterior_info = ft.Text("Selecione uma máquina para ver o horímetro atual.", size=13, color="#616161")
    tf_novo_horimetro = ft.TextField(
        label="Horas Trabalhadas Atuais (Horímetro)",
        keyboard_type=ft.KeyboardType.NUMBER,
        text_size=20,
        prefix_icon="timer",
    )
    coluna_ultimos_apontamentos = ft.Column(spacing=6)

    def ao_mudar_trator_apontamento(e):
        if dd_trator_apontamento.value:
            atual = obter_horimetro_atual(int(dd_trator_apontamento.value))
            txt_horimetro_anterior_info.value = f"Último horímetro registrado: {atual:.1f} horas"
            page.update()

    dd_trator_apontamento.on_change = ao_mudar_trator_apontamento

    def salvar_apontamento(e):
        if not dd_trator_apontamento.value or not tf_novo_horimetro.value:
            mostrar_aviso("Selecione a máquina e digite o horímetro!", "#D32F2F")
            return
        try:
            novas_horas = float(tf_novo_horimetro.value.replace(",", "."))
        except ValueError:
            mostrar_aviso("Digite um valor numérico válido para o horímetro!", "#D32F2F")
            return

        id_maq = int(dd_trator_apontamento.value)
        atual = obter_horimetro_atual(id_maq)
        if novas_horas < atual:
            mostrar_aviso(f"Erro: O horímetro informado ({novas_horas:.0f}h) é menor que o atual ({atual:.0f}h)!", "#D32F2F")
            return

        conn = conectar_bd()
        cursor = conn.cursor()
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
        cursor.execute(
            "INSERT INTO Apontamentos (id_trator, horas_registradas, data) VALUES (?, ?, ?)",
            (id_maq, novas_horas, agora),
        )
        conn.commit()
        conn.close()

        tf_novo_horimetro.value = ""
        txt_horimetro_anterior_info.value = f"Horímetro atualizado para: {novas_horas:.1f} horas"
        mostrar_aviso("Horímetro atualizado! Regras de manutenção recalculadas.")
        carregar_tela_apontamento()

    def carregar_tela_apontamento():
        opcoes = listar_tratores_opcoes()
        dd_trator_apontamento.options = opcoes
        if opcoes and not dd_trator_apontamento.value:
            dd_trator_apontamento.value = opcoes[0].key
            atual = obter_horimetro_atual(int(opcoes[0].key))
            txt_horimetro_anterior_info.value = f"Último horímetro registrado: {atual:.1f} horas"

        conn = conectar_bd()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.data, t.nome, t.placa, a.horas_registradas
            FROM Apontamentos a
            JOIN Tratores t ON a.id_trator = t.id
            ORDER BY a.id DESC LIMIT 6
        """)
        ultimos = cursor.fetchall()
        conn.close()

        coluna_ultimos_apontamentos.controls.clear()
        for u in ultimos:
            coluna_ultimos_apontamentos.controls.append(
                ft.Container(
                    bgcolor="#F5F5F5",
                    padding=8,
                    border_radius=6,
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text(f"{u[1]} ({u[2]})", weight=ft.FontWeight.W_600, size=13),
                            ft.Text(f"{u[3]:.0f}h • {u[0]}", size=12, color="#616161"),
                        ],
                    ),
                )
            )
        page.update()

    tela_apontamento = ft.Container(
        padding=15,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=14,
            controls=[
                ft.Text("Apontamento Diário de Horas", size=20, weight=ft.FontWeight.BOLD),
                dd_trator_apontamento,
                txt_horimetro_anterior_info,
                tf_novo_horimetro,
                ft.ElevatedButton(
                    text="SALVAR HORÍMETRO",
                    icon="save",
                    bgcolor="#2E7D32",
                    color="#FFFFFF",
                    height=50,
                    on_click=salvar_apontamento,
                ),
                ft.Divider(),
                ft.Text("Últimos Apontamentos Registrados", size=15, weight=ft.FontWeight.BOLD),
                coluna_ultimos_apontamentos,
            ],
        ),
    )

    # -----------------------------------------------------------------
    # ABA 3: CADASTROS (MÁQUINAS E REGRAS DE MANUTENÇÃO)
    # -----------------------------------------------------------------
    tf_nome_trator = ft.TextField(label="Nome / Modelo (Ex: Retroescavadeira JCB)")
    tf_placa_trator = ft.TextField(label="Placa ou Código Interno (Ex: JCB-01)")
    tf_horimetro_ini = ft.TextField(label="Horímetro Atual (h)", keyboard_type=ft.KeyboardType.NUMBER, value="0")

    dd_trator_plano = ft.Dropdown(label="Selecione a Máquina")
    dd_categoria_plano = ft.Dropdown(
        label="Categoria da Manutenção",
        value="Preventiva",
        options=[
            ft.dropdown.Option("Preventiva"),
            ft.dropdown.Option("Corretiva"),
            ft.dropdown.Option("Lubrificantes/Filtros"),
            ft.dropdown.Option("Pneus/Material Rodante"),
        ],
    )
    tf_tipo_servico = ft.TextField(label="Serviço / Peça (Ex: Troca de Óleo do Motor)")
    tf_intervalo_horas = ft.TextField(label="Intervalo de Revisão (Ex: a cada 250h)", keyboard_type=ft.KeyboardType.NUMBER, value="250")
    tf_margem_horas = ft.TextField(label="Margem de Segurança / Aviso (Ex: 30h antes)", keyboard_type=ft.KeyboardType.NUMBER, value="30")
    tf_custo_previsto = ft.TextField(label="Custo Previsto R$ (Peças + Mão de Obra)", keyboard_type=ft.KeyboardType.NUMBER, value="500")

    def cadastrar_trator(e):
        if not tf_nome_trator.value or not tf_placa_trator.value:
            mostrar_aviso("Preencha o nome e a placa/código da máquina!", "#D32F2F")
            return
        try:
            h_ini = float(tf_horimetro_ini.value.replace(",", "."))
        except ValueError:
            mostrar_aviso("Horímetro inicial inválido!", "#D32F2F")
            return

        conn = conectar_bd()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Tratores (nome, placa, horimetro_inicial) VALUES (?, ?, ?)",
            (tf_nome_trator.value.strip(), tf_placa_trator.value.strip().upper(), h_ini),
        )
        id_novo = cursor.lastrowid
        cursor.execute(
            "INSERT INTO Apontamentos (id_trator, horas_registradas, data) VALUES (?, ?, ?)",
            (id_novo, h_ini, datetime.now().strftime("%d/%m/%Y %H:%M")),
        )
        conn.commit()
        conn.close()

        tf_nome_trator.value = ""
        tf_placa_trator.value = ""
        tf_horimetro_ini.value = "0"
        carregar_tela_cadastros()
        mostrar_aviso("Máquina cadastrada com sucesso!")

    def cadastrar_plano(e):
        if not dd_trator_plano.value or not tf_tipo_servico.value:
            mostrar_aviso("Selecione a máquina e descreva o serviço!", "#D32F2F")
            return
        try:
            interv = float(tf_intervalo_horas.value.replace(",", "."))
            margem = float(tf_margem_horas.value.replace(",", "."))
            custo = float(tf_custo_previsto.value.replace(",", "."))
        except ValueError:
            mostrar_aviso("Verifique os números de intervalo, margem e custo!", "#D32F2F")
            return

        id_maq = int(dd_trator_plano.value)
        horimetro_base = obter_horimetro_atual(id_maq)

        conn = conectar_bd()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO Planos_Manutencao 
            (id_trator, categoria, tipo, intervalo_horas, margem_aviso_horas, horimetro_ultima_manutencao, custo_previsto)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (id_maq, dd_categoria_plano.value, tf_tipo_servico.value.strip(), interv, margem, horimetro_base, custo),
        )
        conn.commit()
        conn.close()

        tf_tipo_servico.value = ""
        mostrar_aviso("Regra de manutenção adicionada para a máquina!")

    def carregar_tela_cadastros():
        opcoes = listar_tratores_opcoes()
        dd_trator_plano.options = opcoes
        if opcoes and not dd_trator_plano.value:
            dd_trator_plano.value = opcoes[0].key
        page.update()

    tela_cadastros = ft.Container(
        padding=15,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=12,
            controls=[
                ft.Text("1. Cadastrar Nova Máquina / Trator", size=18, weight=ft.FontWeight.BOLD),
                tf_nome_trator,
                ft.Row(controls=[ft.Container(expand=1, content=tf_placa_trator), ft.Container(expand=1, content=tf_horimetro_ini)]),
                ft.ElevatedButton("Cadastrar Máquina", icon="add_circle", bgcolor="#1565C0", color="#FFFFFF", on_click=cadastrar_trator),
                ft.Divider(height=25),
                ft.Text("2. Configurar Regra de Manutenção", size=18, weight=ft.FontWeight.BOLD),
                dd_trator_plano,
                dd_categoria_plano,
                tf_tipo_servico,
                ft.Row(
                    controls=[
                        ft.Container(expand=1, content=tf_intervalo_horas),
                        ft.Container(expand=1, content=tf_margem_horas),
                    ]
                ),
                tf_custo_previsto,
                ft.ElevatedButton("Salvar Plano de Manutenção", icon="build", bgcolor="#2E7D32", color="#FFFFFF", on_click=cadastrar_plano),
            ],
        ),
    )

    # -----------------------------------------------------------------
    # ABA 4: RELATÓRIOS EM PDF E COMPARTILHAMENTO WHATSAPP
    # -----------------------------------------------------------------
    dd_filtro_trator_rel = ft.Dropdown(label="Filtrar por Máquina", value="TODOS")
    dd_orientacao_pdf = ft.Dropdown(
        label="Orientação do PDF para Impressão",
        value="P",
        options=[
            ft.dropdown.Option(key="P", text="Retrato (Vertical)"),
            ft.dropdown.Option(key="L", text="Paisagem (Horizontal)"),
        ],
    )
    txt_caminho_pdf_gerado = ft.Text("", size=12, color="#1565C0", selectable=True)

    def acao_gerar_pdf(e):
        caminho = gerar_relatorio_pdf(
            orientacao=dd_orientacao_pdf.value,
            filtro_trator_id=dd_filtro_trator_rel.value,
        )
        txt_caminho_pdf_gerado.value = f"PDF gerado com sucesso em:\n{caminho}"
        mostrar_aviso("Relatório PDF gerado e salvo no dispositivo!")

    def acao_compartilhar_whatsapp(e):
        itens = calcular_painel_manutencoes(dd_filtro_trator_rel.value)
        linhas_msg = ["*RELATÓRIO DE MANUTENÇÃO E HORÍMETRO*"]
        for i in itens:
            emoji = "🔴" if i["status"] == "ATRASADA" else ("🟡" if i["status"] == "ALERTA" else "🟢")
            linhas_msg.append(
                f"{emoji} *{i['maquina']}*\n"
                f"   • Serviço: {i['tipo']} ({i['categoria']})\n"
                f"   • Horímetro: {i['horimetro_atual']:.0f}h (Meta: {i['proxima_manutencao']:.0f}h)\n"
                f"   • Status: {i['detalhe']}\n"
                f"   • Custo Previsto: R$ {i['custo_previsto']:.2f}"
            )
        texto_completo = "\n\n".join(linhas_msg)
        url_wa = f"https://wa.me/?text={urllib.parse.quote(texto_completo)}"
        page.launch_url(url_wa)

    def carregar_tela_relatorios():
        opcoes = [ft.dropdown.Option(key="TODOS", text="Todas as Máquinas Cadastradas")] + listar_tratores_opcoes()
        dd_filtro_trator_rel.options = opcoes
        if not dd_filtro_trator_rel.value:
            dd_filtro_trator_rel.value = "TODOS"
        page.update()

    tela_relatorios = ft.Container(
        padding=15,
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            spacing=14,
            controls=[
                ft.Text("Relatórios e Exportação", size=20, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Gere relatórios dinâmicos de custos realizados e manutenções planejadas em PDF ou envie o alerta direto para o WhatsApp.",
                    size=13,
                    color="#616161",
                ),
                dd_filtro_trator_rel,
                dd_orientacao_pdf,
                ft.ElevatedButton(
                    text="Gerar Relatório em PDF",
                    icon="picture_as_pdf",
                    bgcolor="#C62828",
                    color="#FFFFFF",
                    height=48,
                    on_click=acao_gerar_pdf,
                ),
                ft.ElevatedButton(
                    text="Compartilhar Alertas e Resumo no WhatsApp",
                    icon="share",
                    bgcolor="#2E7D32",
                    color="#FFFFFF",
                    height=48,
                    on_click=acao_compartilhar_whatsapp,
                ),
                txt_caminho_pdf_gerado,
            ],
        ),
    )

    # -----------------------------------------------------------------
    # NAVEGAÇÃO PRINCIPAL (4 ABAS DO MVP)
    # -----------------------------------------------------------------
    area_conteudo = ft.Container(expand=True, content=tela_dashboard)

    def alternar_aba(indice):
        if indice == 0:
            atualizar_dashboard()
            area_conteudo.content = tela_dashboard
        elif indice == 1:
            carregar_tela_apontamento()
            area_conteudo.content = tela_apontamento
        elif indice == 2:
            carregar_tela_cadastros()
            area_conteudo.content = tela_cadastros
        elif indice == 3:
            carregar_tela_relatorios()
            area_conteudo.content = tela_relatorios
        page.update()

    barra_navegacao = ft.Container(
        bgcolor="#263238",
        padding=ft.padding.symmetric(vertical=6, horizontal=4),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_AROUND,
            controls=[
                ft.TextButton(
                    text="Alertas",
                    icon="warning_amber",
                    style=ft.ButtonStyle(color="#FFFFFF"),
                    on_click=lambda e: alternar_aba(0),
                ),
                ft.TextButton(
                    text="Horímetro",
                    icon="speed",
                    style=ft.ButtonStyle(color="#FFFFFF"),
                    on_click=lambda e: alternar_aba(1),
                ),
                ft.TextButton(
                    text="Cadastros",
                    icon="add_box",
                    style=ft.ButtonStyle(color="#FFFFFF"),
                    on_click=lambda e: alternar_aba(2),
                ),
                ft.TextButton(
                    text="Relatórios",
                    icon="picture_as_pdf",
                    style=ft.ButtonStyle(color="#FFFFFF"),
                    on_click=lambda e: alternar_aba(3),
                ),
            ],
        ),
    )

    cabecalho_app = ft.Container(
        bgcolor="#1B5E20",
        padding=ft.padding.symmetric(horizontal=15, vertical=12),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text("AgroHorímetro • Gestão de Frota", color="#FFFFFF", size=17, weight=ft.FontWeight.BOLD),
                ft.Icon(name="agriculture", color="#FFFFFF"),
            ],
        ),
    )

    page.add(
        ft.Column(
            expand=True,
            spacing=0,
            controls=[
                cabecalho_app,
                area_conteudo,
                barra_navegacao,
            ],
        )
    )

    atualizar_dashboard()


ft.app(target=main)