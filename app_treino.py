# app_treino.py (Versão Final com Modo de Treino Interativo)
"""
FitPro - App completo pronto para deploy
- Spinner em operações de I/O (salvar/carregar)
- Confirmações elegantes (st.dialog() quando disponível, fallback)
- Calendário visual de treinos
- Firebase (Auth + Firestore) via st.secrets["firebase_credentials"]
- Compatibilidade Streamlit (st.rerun fallback)
- Geração de treino totalmente personalizada baseada em questionário.
- Lógica de substituição de exercícios baseada em restrições.
- Banco de exercícios expandido com categorias e alternativas.
- Login persistente com cookies para não deslogar ao atualizar a página.
- Uso de st.cache_resource para otimizar a conexão com Firebase.
- Funcionalidade de Rede Social com posts, fotos, curtidas e comentários.
- Sistema de Seguir/Deixar de Seguir usuários e Feed Personalizado.
- Interface da página "Meu Treino" com busca dinâmica de GIFs na internet.
- [NOVO] Modo de Treino Interativo com checklist, timer de descanso e registro em tempo real.
"""
import os
import re
import urllib.parse
import io
import json
import time
import base64
import logging
import requests  # Importação necessária para buscar GIFs
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from itertools import cycle

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from PIL import Image, ImageChops, ImageFilter, ImageStat
from streamlit_cookies_manager import CookieManager


import random
# Optional SSIM
try:
    from skimage.metrics import structural_similarity as ssim  # type: ignore

    SKIMAGE_AVAILABLE = True
except Exception:
    SKIMAGE_AVAILABLE = False

# Firebase admin
import firebase_admin
from firebase_admin import credentials, auth, firestore

# Suppress noisy logs
os.environ["GRPC_VERBOSITY"] = "NONE"
logging.getLogger("google").setLevel(logging.ERROR)

# ---------------------------
# Streamlit compatibility
# ---------------------------
if not hasattr(st, "rerun") and hasattr(st, "experimental_rerun"):
    st.rerun = st.experimental_rerun  # type: ignore

HAS_ST_DIALOG = hasattr(st, "dialog")
HAS_ST_MODAL = hasattr(st, "modal")

# ---------------------------
# Page config & Cookie Manager
# ---------------------------
st.set_page_config(page_title="FitPro", page_icon="🏋️", layout="wide")
cookies = CookieManager()

if not cookies.ready():
    st.stop()


# ---------------------------
# Helpers
# ---------------------------
def iso_now() -> str:
    return datetime.now().isoformat()


def sha256(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


def valid_email(e: str) -> bool:
    return bool(re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', e or ''))


def b64_from_pil(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def pil_from_b64(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert('RGBA')


def overlay_blend(img1: Image.Image, img2: Image.Image, alpha: float) -> Image.Image:
    img1 = img1.convert('RGBA').resize(img2.size)
    return Image.blend(img1, img2, alpha)


def compare_images_metric(img1: Image.Image, img2: Image.Image) -> Dict[str, Any]:
    img1_s = img1.convert('L').resize((256, 256))
    img2_s = img2.convert('L').resize((256, 256))
    arr1 = np.array(img1_s).astype(float)
    arr2 = np.array(img2_s).astype(float)
    mse = float(((arr1 - arr2) ** 2).mean())
    res = {'mse': mse}
    if SKIMAGE_AVAILABLE:
        try:
            res['ssim'] = float(ssim(arr1, arr2))
        except Exception:
            res['ssim'] = None
    else:
        res['ssim'] = None
    e1 = img1_s.filter(ImageFilter.FIND_EDGES)
    e2 = img2_s.filter(ImageFilter.FIND_EDGES)
    ed = ImageChops.difference(e1, e2)
    stat = ImageStat.Stat(ed)
    res['edge_diff_mean'] = float(np.mean(stat.mean))
    return res


# ---------------------------
# Firebase initialization
# ---------------------------
@st.cache_resource
def init_firebase():
    try:
        creds = dict(st.secrets["firebase_credentials"])
        if "private_key" in creds and isinstance(creds["private_key"], str):
            creds["private_key"] = creds["private_key"].replace('\\n', '\n')
        if not firebase_admin._apps:
            cred = credentials.Certificate(creds)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        st.error("Erro inicializando Firebase. Verifique st.secrets['firebase_credentials'].")
        st.error(str(e))
        st.stop()


if 'db' not in st.session_state:
    st.session_state['db'] = init_firebase()
db = st.session_state['db']


# ---------------------------
# Session defaults
# ---------------------------
def ensure_session_defaults():
    defaults = {
        'usuario_logado': None,
        'user_uid': None,
        'dados_usuario': None,
        'plano_treino': None,
        'frequencia': [],
        'historico_treinos': [],
        'historico_peso': [],
        'metas': [],
        'fotos_progresso': [],
        'medidas': [],
        'feedbacks': [],
        'ciclo_atual': None,
        'role': None,
        'notificacoes': [],
        'settings': {'theme': 'light', 'notify_on_login': True},
        'offline_mode': False,
        'confirm_excluir_foto': False,
        'foto_a_excluir': None,

        # Variáveis para o Modo Treino
        'workout_in_progress': False,
        'current_workout_plan': None,
        'current_exercise_index': 0,
        'workout_log': [],
        'rest_timer_end': None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


ensure_session_defaults()


# ---------------------------
# Função para buscar GIF de exercício
# ---------------------------
@st.cache_data(ttl=3600 * 24)  # Cache de 24 horas
def find_exercise_video_youtube(exercise_name: str) -> Optional[str]:
    """Busca vídeo no YouTube via scraping e regex, retorna URL."""
    # st.write(f"--- Iniciando busca para: {exercise_name} ---") # DEBUG
    search_terms = [
        f"como fazer {exercise_name} execução correta",
        f"{exercise_name} tutorial pt-br",
        f"{exercise_name} exercise tutorial",
        f"{exercise_name} exercise form",
        exercise_name
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    for term in search_terms:
        try:
            # st.write(f"Tentando busca com termo: '{term}'") # DEBUG
            query = urllib.parse.urlencode({'search_query': term})
            url = f"https://www.youtube.com/results?{query}"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            html_content = response.text
            video_ids = re.findall(r'"/watch\?v=([a-zA-Z0-9_-]{11})"', html_content)
            # st.write(f"IDs encontrados para '{term}': {video_ids}") # DEBUG

            if video_ids:
                first_unique_id = None
                seen_ids = set()
                for video_id in video_ids:
                    if video_id not in seen_ids:
                        first_unique_id = video_id
                        seen_ids.add(video_id)
                        break
                if first_unique_id:
                    video_url = f"https://www.youtube.com/watch?v={first_unique_id}"
                    # st.write(f"*** Encontrado vídeo: {video_url} ***") # DEBUG
                    return video_url
        except requests.exceptions.RequestException as e:
            # st.write(f"!!! Erro de rede durante a busca por '{term}': {e}") # DEBUG
            time.sleep(1)
            continue
        except Exception as e:
            # st.write(f"!!! Erro geral durante a busca por '{term}': {e}") # DEBUG
            continue
    # st.write(f"--- Busca finalizada para {exercise_name}, nenhum vídeo encontrado. ---") # DEBUG
    return None

def trocar_exercicio(nome_treino, exercise_index, exercicio_atual):
    """Substitui um exercício por outro do mesmo grupo muscular."""
    try:
        # 1. Encontrar o grupo muscular do exercício a ser trocado
        grupo_muscular = EXERCICIOS_DB.get(exercicio_atual, {}).get('grupo')
        if not grupo_muscular:
            st.warning("Não foi possível identificar o grupo muscular para encontrar um substituto.")
            return

        # 2. Encontrar todos os exercícios candidatos do mesmo grupo
        df_treino_atual = pd.DataFrame(st.session_state['plano_treino'][nome_treino])
        exercicios_no_plano = set(df_treino_atual['Exercício'])

        candidatos = [
            ex for ex, details in EXERCICIOS_DB.items()
            if details.get('grupo') == grupo_muscular and ex not in exercicios_no_plano
        ]

        # 3. Se houver candidatos, escolher um e fazer a troca
        if candidatos:
            novo_exercicio = random.choice(candidatos)

            # Atualiza o DataFrame no session_state
            df_para_atualizar = st.session_state['plano_treino'][nome_treino]
            # Convertendo para DataFrame para manipulação segura
            df_manipulavel = pd.DataFrame(df_para_atualizar)
            df_manipulavel.loc[exercise_index, 'Exercício'] = novo_exercicio

            # Salva de volta no formato de lista de dicionários
            st.session_state['plano_treino'][nome_treino] = df_manipulavel.to_dict('records')

            st.toast(f"'{exercicio_atual}' trocado por '{novo_exercicio}'!")

            # 4. Salvar a alteração no Firebase
            salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
        else:
            st.warning("Nenhum exercício alternativo encontrado para este grupo muscular.")

    except Exception as e:
        st.error(f"Ocorreu um erro ao tentar trocar o exercício: {e}")

def trocar_exercicio(nome_treino, exercise_index, exercicio_atual):
    """Substitui um exercício por outro do mesmo grupo muscular."""
    try:
        # 1. Encontrar o grupo muscular do exercício a ser trocado
        grupo_muscular = EXERCICIOS_DB.get(exercicio_atual, {}).get('grupo')
        if not grupo_muscular:
            st.warning("Não foi possível identificar o grupo muscular para encontrar um substituto.")
            return

        # 2. Encontrar todos os exercícios candidatos do mesmo grupo
        df_treino_atual = pd.DataFrame(st.session_state['plano_treino'][nome_treino])
        exercicios_no_plano = set(df_treino_atual['Exercício'])

        candidatos = [
            ex for ex, details in EXERCICIOS_DB.items()
            if details.get('grupo') == grupo_muscular and ex not in exercicios_no_plano
        ]

        # 3. Se houver candidatos, escolher um e fazer a troca
        if candidatos:
            novo_exercicio = random.choice(candidatos)

            # Atualiza o DataFrame no session_state
            df_para_atualizar = st.session_state['plano_treino'][nome_treino]
            # Convertendo para DataFrame para manipulação segura
            df_manipulavel = pd.DataFrame(df_para_atualizar)
            df_manipulavel.loc[exercise_index, 'Exercício'] = novo_exercicio

            # Salva de volta no formato de lista de dicionários
            st.session_state['plano_treino'][nome_treino] = df_manipulavel.to_dict('records')

            st.toast(f"'{exercicio_atual}' trocado por '{novo_exercicio}'!")

            # 4. Salvar a alteração no Firebase
            salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
        else:
            st.warning("Nenhum exercício alternativo encontrado para este grupo muscular.")

    except Exception as e:
        st.error(f"Ocorreu um erro ao tentar trocar o exercício: {e}")

# ---------------------------
# Banco de Exercícios Expandido
# ---------------------------
EXERCICIOS_DB = {
    # Pernas (Foco Quadríceps/Geral)
    'Agachamento com Barra': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar', 'Joelhos'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra apoiada nos ombros/trapézio. Pés afastados na largura dos ombros. Desça flexionando quadril e joelhos, mantendo a coluna neutra e o peito aberto. Suba estendendo quadril e joelhos.'
    },
    'Agachamento com Halteres': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Joelhos'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Segure halteres ao lado do corpo com as palmas voltadas para dentro. Mantenha o tronco ereto, desça flexionando quadril e joelhos. Suba estendendo.'
    },
    'Agachamento Goblet': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Joelhos'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Segure um halter verticalmente contra o peito. Pés levemente mais afastados que os ombros. Desça o mais fundo possível, mantendo o tronco ereto e os cotovelos entre os joelhos. Suba.'
    },
    'Leg Press 45°': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sente-se na máquina com as costas bem apoiadas. Pés na plataforma afastados na largura dos ombros. Destrave e desça controladamente flexionando os joelhos (aprox. 90°). Empurre de volta à posição inicial sem travar os joelhos.'
    },
    'Cadeira Extensora': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sente-se na máquina, ajuste o apoio dos tornozelos. Estenda completamente os joelhos, levantando o peso. Retorne controladamente à posição inicial.'
    },
    # Pernas (Foco Posterior)
    'Mesa Flexora': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deite-se de bruços na máquina, joelhos alinhados com o eixo, tornozelos sob o apoio. Flexione os joelhos trazendo os calcanhares em direção aos glúteos. Retorne controladamente.'
    },
    'Stiff com Halteres': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Lombar'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres na frente das coxas. Mantenha os joelhos levemente flexionados (quase estendidos). Desça o tronco projetando o quadril para trás, mantendo a coluna reta e os halteres próximos às pernas. Suba contraindo posteriores e glúteos.'
    },
    # Glúteos (Considerados como parte de 'Pernas')
    'Elevação Pélvica': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Barra', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas com os ombros apoiados em um banco e joelhos flexionados. Apoie uma barra sobre o quadril. Desça o quadril e eleve-o o máximo possível, contraindo os glúteos no topo. Controle a descida.'
    },
    'Extensão de Quadril (Coice)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal/Caneleiras/Polia', 'restricoes': ['Lombar'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em quatro apoios ou em pé na polia/com caneleiras. Estenda uma perna para trás e para cima, contraindo o glúteo. Mantenha o abdômen contraído e evite arquear a lombar. Retorne controladamente.'
    },
    'Abdução de Quadril': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina/Elásticos/Peso Corporal', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina abdutora, deitado de lado, ou em pé com elásticos/caneleiras. Afaste a(s) perna(s) lateralmente contra a resistência, focando no glúteo lateral (médio/mínimo). Retorne controladamente.'
    },
    'Glúteo Sapinho (Frog Pump)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, junte as solas dos pés e afaste os joelhos (posição de "sapo"). Calcanhares próximos aos glúteos. Eleve o quadril do chão, contraindo fortemente os glúteos. Desça controladamente.'
     },
    # Panturrilhas
    'Panturrilha no Leg Press': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado no Leg Press, ponta dos pés na parte inferior da plataforma, calcanhares para fora. Joelhos estendidos (não travados). Empurre a plataforma apenas com a flexão plantar. Retorne alongando.'
    },

    # Peito
    'Supino Reto com Barra': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Ombros'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, pés firmes no chão. Pegada na barra um pouco mais larga que os ombros. Desça a barra controladamente até tocar levemente o meio do peito. Empurre a barra de volta para cima.'
    },
    'Supino Reto com Halteres': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, segure os halteres acima do peito com as palmas para frente. Desça os halteres lateralmente, flexionando os cotovelos. Empurre os halteres de volta para cima.'
    },
    'Supino Inclinado com Halteres': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado em um banco inclinado (30-45°). Movimento similar ao supino reto com halteres, mas descendo os pesos em direção à parte superior do peito.'
    },
    'Crucifixo com Halteres': {
        'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, halteres acima do peito, palmas das mãos voltadas uma para a outra, cotovelos levemente flexionados. Abra os braços descendo os halteres lateralmente em um arco. Retorne à posição inicial contraindo o peito.'
    },
    'Flexão de Braço': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Punhos'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Mãos no chão afastadas na largura dos ombros (ou um pouco mais). Corpo reto da cabeça aos calcanhares. Desça o peito flexionando os cotovelos. Empurre de volta à posição inicial.'
    },

    # Costas
    'Barra Fixa': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': [], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Pendure-se na barra com pegada pronada (palmas para frente) ou supinada (palmas para você), mãos afastadas na largura dos ombros ou mais. Puxe o corpo para cima até o queixo passar a barra, contraindo as costas. Desça controladamente.'
    },
    'Puxada Alta (Lat Pulldown)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina, ajuste o apoio dos joelhos. Pegada na barra mais larga que os ombros. Puxe a barra verticalmente em direção à parte superior do peito, mantendo o tronco estável e contraindo as costas. Retorne controladamente.'
    },
    'Remada Curvada com Barra': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Incline o tronco à frente (45-60°), mantendo a coluna reta e os joelhos levemente flexionados. Pegada pronada na barra. Puxe a barra em direção ao abdômen/peito baixo, contraindo as costas. Desça controladamente.'
    },
    'Remada Sentada (máquina)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina com o peito apoiado (se houver). Puxe as manoplas/pegadores em direção ao corpo, mantendo os cotovelos próximos ao tronco e contraindo as escápulas. Retorne controladamente.'
    },
    'Remada Unilateral (Serrote)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Apoie um joelho e a mão do mesmo lado em um banco. Mantenha o tronco paralelo ao chão e a coluna reta. Com o outro braço, puxe o halter em direção ao quadril/costela, mantendo o cotovelo próximo ao corpo. Desça controladamente.'
    },

    # Ombros
    'Desenvolvimento Militar com Barra': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar', 'Ombros'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), barra apoiada na parte superior do peito, pegada pronada um pouco mais larga que os ombros. Empurre a barra verticalmente para cima até estender os cotovelos. Desça controladamente até a posição inicial.'
    },
    'Desenvolvimento com Halteres (sentado)': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado em um banco com encosto, segure os halteres na altura dos ombros com as palmas para frente. Empurre os halteres verticalmente para cima. Desça controladamente.'
    },
    'Elevação Lateral': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres ao lado do corpo. Mantenha os cotovelos levemente flexionados. Eleve os braços lateralmente até a altura dos ombros. Desça controladamente.'
    },
    'Elevação Frontal': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres na frente das coxas (pegada pronada ou neutra). Eleve um braço de cada vez (ou ambos) para frente, mantendo o cotovelo levemente flexionado, até a altura dos ombros. Desça controladamente.'
    },

    # Bíceps
    'Rosca Direta com Barra': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': ['Punhos'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada supinada (palmas para cima), mãos na largura dos ombros. Mantenha os cotovelos fixos ao lado do corpo. Flexione os cotovelos trazendo a barra em direção aos ombros. Desça controladamente.'
    },
    'Rosca Direta com Halteres': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), segure halteres ao lado do corpo com pegada supinada. Mantenha os cotovelos fixos. Flexione os cotovelos, elevando os halteres. Pode ser feito simultaneamente ou alternadamente. Desça controladamente.'
    },
    'Rosca Martelo': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), segure halteres ao lado do corpo com pegada neutra (palmas voltadas para o corpo). Mantenha os cotovelos fixos. Flexione os cotovelos, elevando os halteres. Desça controladamente.'
    },

    # Tríceps
    'Tríceps Testa': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': ['Cotovelos'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado em um banco reto, segure uma barra W (ou halteres com pegada neutra) acima do peito com os braços estendidos. Mantenha os braços (úmeros) parados. Flexione os cotovelos descendo o peso em direção à testa/cabeça. Estenda os cotovelos de volta à posição inicial.'
    },
    'Tríceps Pulley': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, de frente para a polia alta, segure a barra ou corda com pegada pronada (ou neutra na corda). Mantenha os cotovelos fixos ao lado do corpo. Estenda completamente os cotovelos empurrando a barra/corda para baixo. Retorne controladamente.'
    },
    'Mergulho no Banco': {
        'grupo': 'Tríceps', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Ombros', 'Punhos'], 'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Apoie as mãos em um banco atrás do corpo, dedos para frente. Mantenha as pernas estendidas à frente (ou joelhos flexionados para facilitar). Flexione os cotovelos descendo o corpo verticalmente. Empurre de volta para cima estendendo os cotovelos.'
    },

    # Core
    'Prancha': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Apoie os antebraços e as pontas dos pés no chão. Mantenha o corpo reto da cabeça aos calcanhares, contraindo o abdômen e os glúteos. Evite elevar ou baixar demais o quadril. Sustente a posição.'
    },
    'Abdominal Crunch': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, joelhos flexionados e pés no chão (ou pernas elevadas). Mãos atrás da cabeça (sem puxar) ou cruzadas no peito. Eleve a cabeça e os ombros do chão, contraindo o abdômen ("enrolando" a coluna). Retorne controladamente.'
    },
    'Elevação de Pernas': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'], # Iniciante com cuidado/adaptação
        'descricao': 'Deitado de costas, pernas estendidas. Pode colocar as mãos sob a lombar para apoio. Mantendo as pernas retas (ou levemente flexionadas), eleve-as até formarem 90° com o tronco. Desça controladamente quase até o chão, sem deixar a lombar arquear.'
     },
    'Superman': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'], 'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de bruços, braços e pernas estendidos. Eleve simultaneamente braços, peito e pernas do chão, contraindo lombar e glúteos. Mantenha por um instante e retorne controladamente.'
     },
}

EXERCISE_SUBSTITUTIONS = {
    # Substituições PRINCIPALMENTE por RESTRIÇÃO
    'Agachamento com Barra': 'Leg Press 45°', # Lombar, Joelhos -> Máquina
    'Stiff com Halteres': 'Mesa Flexora', # Lombar -> Máquina Isolado
    'Remada Curvada com Barra': 'Remada Sentada (máquina)', # Lombar -> Máquina
    'Desenvolvimento Militar com Barra': 'Desenvolvimento com Halteres (sentado)', # Lombar, Ombros -> Halteres Sentado
    'Supino Reto com Barra': 'Supino Reto com Halteres', # Ombros -> Halteres (maior liberdade)
    'Tríceps Testa': 'Tríceps Pulley', # Cotovelos -> Máquina
    'Rosca Direta com Barra': 'Rosca Direta com Halteres', # Punhos -> Halteres
    'Flexão de Braço': 'Supino Reto com Halteres', # Punhos -> Halteres (menos carga direta no punho)
    'Elevação de Pernas': 'Prancha', # Lombar -> Isométrico seguro
    'Superman': 'Prancha', # Lombar -> Isométrico seguro

    # Substituições PRINCIPALMENTE por NÍVEL (Iniciante não pode fazer)
    'Barra Fixa': 'Puxada Alta (Lat Pulldown)', # Difícil -> Máquina
    'Mergulho no Banco': 'Tríceps Pulley', # Difícil/Ombros -> Máquina
}


# ---------------------------
# Plan serialization helpers
# ---------------------------
def plan_to_serial(plano: Optional[Dict[str, Any]]):
    if not plano:
        return None
    out = {}
    for k, v in plano.items():
        if isinstance(v, pd.DataFrame):
            out[k] = v.to_dict(orient='records')
        else:
            out[k] = v
    return out


def serial_to_plan(serial: Optional[Dict[str, Any]]):
    if not serial:
        return None
    out = {}
    for k, v in serial.items():
        if isinstance(v, list):
            try:
                out[k] = pd.DataFrame(v)
            except Exception:
                out[k] = v
        else:
            out[k] = v
    return out


# ---------------------------
# Firestore save/load (with spinner)
# ---------------------------
def salvar_dados_usuario_firebase(uid: str):
    if not uid or uid == 'demo-uid':
        return
    try:
        with st.spinner("💾 Salvando dados no Firestore..."):
            doc = db.collection('usuarios').document(uid)
            plano_serial = plan_to_serial(st.session_state.get('plano_treino'))
            freq = []
            for d in st.session_state.get('frequencia', []):
                if isinstance(d, (date, datetime)):
                    if isinstance(d, date) and not isinstance(d, datetime):
                        freq.append(datetime.combine(d, datetime.min.time()))
                    else:
                        freq.append(d)
                else:
                    freq.append(d)
            hist = []
            for t in st.session_state.get('historico_treinos', []):
                copy = dict(t)
                if 'data' in copy and isinstance(copy['data'], date) and not isinstance(copy['data'], datetime):
                    copy['data'] = datetime.combine(copy['data'], datetime.min.time())
                hist.append(copy)
            metas_save = []
            for m in st.session_state.get('metas', []):
                copy = dict(m)
                if 'prazo' in copy and isinstance(copy['prazo'], date):
                    copy['prazo'] = datetime.combine(copy['prazo'], datetime.min.time())
                metas_save.append(copy)
            fotos_save = []
            for f in st.session_state.get('fotos_progresso', []):
                copy = dict(f)
                if 'data' in copy and isinstance(copy['data'], date):
                    copy['data'] = copy['data'].isoformat()
                fotos_save.append(copy)
            payload = {
                'dados_usuario': st.session_state.get('dados_usuario'),
                'plano_treino': plano_serial,
                'frequencia': freq,
                'historico_treinos': hist,
                'historico_peso': st.session_state.get('historico_peso', []),
                'metas': metas_save,
                'fotos_progresso': fotos_save,
                'medidas': st.session_state.get('medidas', []),
                'feedbacks': st.session_state.get('feedbacks', []),
                'ciclo_atual': st.session_state.get('ciclo_atual'),
                'role': st.session_state.get('role'),
                'settings': st.session_state.get('settings', {}),
                'ultimo_save': datetime.now()
            }
            doc.set(payload, merge=True)
            time.sleep(0.4)
        st.success("✅ Dados salvos!")
    except Exception as e:
        st.error("Erro ao salvar no Firestore:")
        st.error(str(e))


def carregar_dados_usuario_firebase(uid: str):
    if not uid:
        return
    try:
        with st.spinner("🔁 Carregando dados do Firestore..."):
            doc = db.collection('usuarios').document(uid).get()
            time.sleep(0.2)
        if not doc.exists:
            st.warning("Documento do usuário não encontrado no Firestore.")
            return
        data = doc.to_dict()
        st.session_state['dados_usuario'] = data.get('dados_usuario')
        st.session_state['plano_treino'] = serial_to_plan(data.get('plano_treino'))
        freq = []
        for d in data.get('frequencia', []):
            if isinstance(d, datetime):
                freq.append(d.date())
            elif isinstance(d, str):
                try:
                    freq.append(date.fromisoformat(d))
                except:
                    try:
                        freq.append(datetime.fromisoformat(d).date())
                    except:
                        pass
            else:
                freq.append(d)
        st.session_state['frequencia'] = freq
        hist = data.get('historico_treinos', [])
        for t in hist:
            if 'data' in t and isinstance(t['data'], datetime):
                t['data'] = t['data'].date()
            elif 'data' in t and isinstance(t['data'], str):
                try:
                    t['data'] = date.fromisoformat(t['data'])
                except:
                    pass
        st.session_state['historico_treinos'] = hist
        st.session_state['fotos_progresso'] = data.get('fotos_progresso', [])
        st.session_state['medidas'] = data.get('medidas', [])
        st.session_state['feedbacks'] = data.get('feedbacks', [])
        st.session_state['metas'] = data.get('metas', [])
        st.session_state['role'] = data.get('role')
        st.session_state['settings'] = data.get('settings', st.session_state.get('settings', {}))
    except Exception as e:
        st.error("Erro ao carregar do Firestore:")
        st.error(str(e))


# ---------------------------
# Funções para a Rede Social
# ---------------------------
@st.cache_data(ttl=120)
def carregar_feed_firebase(user_uid: str, limit=50):
    if not user_uid:
        return []
    following_uids = get_following_list(user_uid)
    uids_to_show = list(set(following_uids + [user_uid]))
    if not uids_to_show:
        return []
    try:
        posts_ref = db.collection('posts').where('user_uid', 'in', uids_to_show).order_by('timestamp',
                                                                                          direction=firestore.Query.DESCENDING).limit(
            limit)
        return [doc.to_dict() | {'id': doc.id} for doc in posts_ref.stream()]
    except Exception as e:
        st.error(f"Erro ao carregar o feed: {e}")
        return []


def salvar_post_firebase(user_uid, username, text_content=None, image_b64=None):
    if not user_uid or not username:
        st.error("Usuário não identificado para postar.")
        return False
    if not text_content and not image_b64:
        st.warning("O post precisa de texto ou imagem.")
        return False
    try:
        post_data = {'user_uid': user_uid, 'username': username, 'text_content': text_content, 'image_b64': image_b64,
                     'like_count': 0, 'comment_count': 0, 'timestamp': firestore.SERVER_TIMESTAMP}
        db.collection('posts').add(post_data)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao salvar o post: {e}")
        return False


def _toggle_like_transaction(transaction, post_ref, like_ref):
    like_doc = like_ref.get(transaction=transaction)
    if like_doc.exists:
        transaction.delete(like_ref)
        transaction.update(post_ref, {'like_count': firestore.Increment(-1)})
    else:
        transaction.set(like_ref, {'timestamp': firestore.SERVER_TIMESTAMP})
        transaction.update(post_ref, {'like_count': firestore.Increment(1)})


def curtir_post(post_id, user_uid):
    if not user_uid or not post_id: return
    post_ref = db.collection('posts').document(post_id)
    like_ref = post_ref.collection('likes').document(user_uid)
    db.run_transaction(lambda transaction: _toggle_like_transaction(transaction, post_ref, like_ref))
    st.cache_data.clear()


def comentar_post(post_id, user_uid, username, text):
    if not all([user_uid, post_id, username, text]): return
    try:
        post_ref = db.collection('posts').document(post_id)
        comments_ref = post_ref.collection('comments')
        comment_data = {'user_uid': user_uid, 'username': username, 'text': text,
                        'timestamp': firestore.SERVER_TIMESTAMP}
        comments_ref.add(comment_data)
        post_ref.update({'comment_count': firestore.Increment(1)})
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Erro ao comentar: {e}")
        return False


@st.cache_data(ttl=300)
def carregar_comentarios(post_id):
    try:
        comments_ref = db.collection('posts').document(post_id).collection('comments').order_by('timestamp',
                                                                                                direction=firestore.Query.ASCENDING)
        return [doc.to_dict() for doc in comments_ref.stream()]
    except Exception:
        return []


@st.cache_data(ttl=600)
def get_all_users():
    try:
        users_ref = db.collection('usuarios').stream()
        return [{'id': user.id, 'username': user.to_dict().get('username', 'Usuário Anônimo')} for user in users_ref]
    except Exception as e:
        st.error(f"Erro ao buscar usuários: {e}")
        return []


@st.cache_data(ttl=300)
def get_following_list(user_uid: str) -> List[str]:
    if not user_uid:
        return []
    try:
        following_ref = db.collection('usuarios').document(user_uid).collection('following').stream()
        return [doc.id for doc in following_ref]
    except Exception:
        return []


def follow_user(follower_uid: str, followed_uid: str):
    if not follower_uid or not followed_uid or follower_uid == followed_uid:
        return
    batch = db.batch()
    following_ref = db.collection('usuarios').document(follower_uid).collection('following').document(followed_uid)
    batch.set(following_ref, {'timestamp': firestore.SERVER_TIMESTAMP})
    followers_ref = db.collection('usuarios').document(followed_uid).collection('followers').document(follower_uid)
    batch.set(followers_ref, {'timestamp': firestore.SERVER_TIMESTAMP})
    batch.commit()
    st.cache_data.clear()


def unfollow_user(follower_uid: str, followed_uid: str):
    if not follower_uid or not followed_uid:
        return
    batch = db.batch()
    following_ref = db.collection('usuarios').document(follower_uid).collection('following').document(followed_uid)
    batch.delete(following_ref)
    followers_ref = db.collection('usuarios').document(followed_uid).collection('followers').document(follower_uid)
    batch.delete(followers_ref)
    batch.commit()
    st.cache_data.clear()


# ---------------------------
# Auth helpers
# ---------------------------
def criar_usuario_firebase(email: str, senha: str, nome: str) -> (bool, str):
    try:
        try:
            _ = auth.get_user_by_email(email)
            return False, "Já existe um usuário com esse e-mail."
        except auth.UserNotFoundError:
            pass
        user = auth.create_user(email=email, password=senha, display_name=nome)
        uid = user.uid
        db.collection('usuarios').document(uid).set({
            'email': email, 'username': nome, 'dados_usuario': {'nome': nome},
            'plano_treino': None, 'frequencia': [], 'historico_treinos': [],
            'historico_peso': [], 'metas': [], 'fotos_progresso': [], 'medidas': [],
            'feedbacks': [], 'ciclo_atual': None, 'role': None, 'password_hash': sha256(senha),
            'data_criacao': datetime.now()
        })
        return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"


def verificar_credenciais_firebase(username_or_email: str, senha: str) -> (bool, str):
    if username_or_email == 'demo' and senha == 'demo123':
        st.session_state['user_uid'] = 'demo-uid'
        st.session_state['usuario_logado'] = 'Demo'
        doc = db.collection('usuarios').document('demo-uid').get()
        if doc.exists:
            carregar_dados_usuario_firebase('demo-uid')
        else:
            st.session_state['dados_usuario'] = {'nome': 'Demo', 'peso': 75, 'altura': 175,
                                                 'nivel': 'Intermediário/Avançado', 'dias_semana': 4,
                                                 'objetivo': 'Hipertrofia', 'restricoes': ['Lombar']}
            st.session_state['plano_treino'] = gerar_plano_personalizado(st.session_state['dados_usuario'])
            st.session_state['frequencia'] = []
            st.session_state['historico_treinos'] = []
            st.session_state['metas'] = []
            st.session_state['fotos_progresso'] = []
        return True, "Modo demo ativado."
    try:
        user = auth.get_user_by_email(username_or_email)
        uid = user.uid
        doc = db.collection('usuarios').document(uid).get()
        if not doc.exists:
            return False, "Usuário sem documento no Firestore."
        data = doc.to_dict()
        stored_hash = data.get('password_hash')
        if stored_hash and stored_hash == sha256(senha):
            st.session_state['user_uid'] = uid
            st.session_state['usuario_logado'] = data.get('username') or username_or_email
            carregar_dados_usuario_firebase(uid)
            cookies['user_uid'] = uid
            return True, f"Bem-vindo(a), {st.session_state['usuario_logado']}!"
        else:
            return False, "Senha incorreta."
    except auth.UserNotFoundError:
        return False, "Usuário não encontrado."
    except Exception as e:
        return False, f"Erro ao autenticar: {e}"


# ---------------------------
# Periodization & Notifications
# ---------------------------
def verificar_periodizacao(num_treinos: int):
    TREINOS = 20
    ciclo = num_treinos // TREINOS
    fase_idx = ciclo % 3
    treinos_no_ciclo = num_treinos % TREINOS
    fases = [
        {'nome': 'Hipertrofia', 'series': '3-4', 'reps': '8-12', 'descanso': '60-90s', 'cor': '#FF6B6B'},
        {'nome': 'Força', 'series': '4-5', 'reps': '4-6', 'descanso': '120-180s', 'cor': '#4ECDC4'},
        {'nome': 'Resistência', 'series': '2-3', 'reps': '15-20', 'descanso': '30-45s', 'cor': '#95E1D3'},
    ]
    return {'fase_atual': fases[fase_idx], 'treinos_restantes': TREINOS - treinos_no_ciclo,
            'proxima_fase': fases[(fase_idx + 1) % 3], 'numero_ciclo': ciclo + 1}


def check_notifications_on_open():
    notifs = []
    dados = st.session_state.get('dados_usuario') or {}
    dias_list = dados.get('dias_semana_list') or None
    if dias_list and st.session_state['settings'].get('notify_on_login', True):
        hoje = datetime.now().weekday()
        if hoje in dias_list:
            notifs.append({'tipo': 'lembrete_treino', 'msg': 'Hoje é dia de treino! Confira seu plano.'})
    for m in st.session_state.get('metas', []):
        prazo = m.get('prazo')
        try:
            prazo_dt = date.fromisoformat(prazo) if isinstance(prazo, str) else prazo
            dias = (prazo_dt - datetime.now().date()).days
            if 0 <= dias <= 3:
                notifs.append({'tipo': 'meta', 'msg': f"Meta '{m.get('descricao')}' vence em {dias} dia(s)."})
        except:
            pass
    num_treinos = len(set(st.session_state.get('frequencia', [])))
    info = verificar_periodizacao(num_treinos)
    if info['treinos_restantes'] <= 0 and st.session_state.get('ciclo_atual') != info['numero_ciclo']:
        notifs.append({'tipo': 'nova_fase',
                       'msg': f"👏 Novo ciclo iniciado: {info['fase_atual']['nome']} (Ciclo {info['numero_ciclo']})"})
        st.session_state['ciclo_atual'] = info['numero_ciclo']
        if dados:
            st.session_state['plano_treino'] = gerar_plano_personalizado(dados, info['fase_atual'])
            notifs.append({'tipo': 'plano_ajustado', 'msg': 'Seu plano foi ajustado para a nova fase de treino!'})
    for t in (5, 10, 30, 50, 100):
        if num_treinos == t:
            notifs.append({'tipo': 'conquista', 'msg': f"🎉 Você alcançou {t} treinos!"})
    st.session_state['notificacoes'] = notifs


# ---------------------------
# UI & Plan Generation
# ---------------------------
def show_logo_center():
    st.markdown("<div style='text-align:center;'><h1>🏋️ FitPro</h1><p>Seu Personal Trainer Digital</p></div>",
                unsafe_allow_html=True)


def confirm_delete_photo_dialog(idx: int, uid: Optional[str]):
    if HAS_ST_DIALOG:
        @st.dialog("🗑️ Confirmar Exclusão")
        def inner():
            st.write("Deseja realmente excluir esta foto? Esta ação é irreversível.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("❌ Cancelar"):
                    st.rerun()
            with c2:
                if st.button("✅ Confirmar"):
                    fotos = st.session_state.get('fotos_progresso', [])
                    if 0 <= idx < len(fotos):
                        fotos.pop(idx)
                        st.session_state['fotos_progresso'] = fotos
                        if uid:
                            salvar_dados_usuario_firebase(uid)
                        st.success("Foto excluída.")
                        st.rerun()

        inner()
    else:
        st.session_state['foto_a_excluir'] = idx
        st.session_state['confirm_excluir_foto'] = True


def gerar_plano_personalizado(dados_usuario: Dict[str, Any], fase_atual: Optional[Dict] = None) -> Dict:
    # Pega os dados do usuário, incluindo o nível
    nivel_usuario = dados_usuario.get('nivel', 'Iniciante')
    dias = dados_usuario.get('dias_semana', 3)
    objetivo = dados_usuario.get('objetivo', 'Hipertrofia')
    restricoes_usr = dados_usuario.get('restricoes', [])
    sexo = dados_usuario.get('sexo', 'Masculino')

    # Define séries/reps/descanso base
    if fase_atual:
        series_base, reps_base, descanso_base = fase_atual['series'], fase_atual['reps'], fase_atual['descanso']
    else:
        if objetivo == 'Hipertrofia':
            series_base, reps_base, descanso_base = '3-4', '8-12', '60-90s'
        elif objetivo == 'Emagrecimento':
            series_base, reps_base, descanso_base = '3', '12-15', '45-60s'
        else:
            series_base, reps_base, descanso_base = '3', '15-20', '30-45s'

    # Função selecionar_exercicios agora filtra por niveis_permitidos
    def selecionar_exercicios(grupos: List[str], n_compostos: int, n_isolados: int, excluir: List[str] = []) -> List[
        Dict]:
        exercicios_selecionados = []
        candidatos_validos = []

        exercicios_considerados = list(EXERCICIOS_DB.items())
        random.shuffle(exercicios_considerados)  # Embaralha para variar a seleção inicial

        for ex_nome, ex_data in exercicios_considerados:
            if ex_data.get('grupo') in grupos and ex_nome not in excluir:

                # --- FILTRO DE NÍVEL (usando niveis_permitidos) ---
                niveis_permitidos = ex_data.get('niveis_permitidos', ['Iniciante',
                                                                      'Intermediário/Avançado'])  # Assume todos se não especificado
                # PULA se o nível do usuário NÃO ESTÁ na lista de níveis permitidos do exercício
                if nivel_usuario not in niveis_permitidos:
                    continue
                # --- FIM DO FILTRO DE NÍVEL ---

                # Lógica de restrição
                exercicio_tem_restricao = any(r in ex_data.get('restricoes', []) for r in restricoes_usr)
                if exercicio_tem_restricao:
                    substituto = EXERCISE_SUBSTITUTIONS.get(ex_nome)
                    if substituto and substituto not in excluir:
                        sub_details = EXERCICIOS_DB.get(substituto, {})
                        sub_niveis = sub_details.get('niveis_permitidos', ['Iniciante', 'Intermediário/Avançado'])

                        # Verifica se o SUBSTITUTO é adequado para o nível E não tem restrição
                        if nivel_usuario not in sub_niveis:
                            continue  # Substituto não é para este nível
                        elif substituto not in candidatos_validos and not any(
                                r in sub_details.get('restricoes', []) for r in restricoes_usr):
                            candidatos_validos.append(substituto)  # Adiciona substituto válido

                # Se o exercício original passou no filtro de nível E não tem restrição
                elif ex_nome not in candidatos_validos:
                    candidatos_validos.append(ex_nome)  # Adiciona original válido

        # Lógica de seleção (com fallback)
        # candidatos = list(set(candidatos_validos)) # Set já não é necessário se a lógica acima estiver correta
        candidatos = candidatos_validos  # Já deve ter apenas válidos e únicos
        compostos_selecionados = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] == 'Composto']
        isolados_selecionados = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] != 'Composto']
        compostos_finais = compostos_selecionados[:n_compostos]
        isolados_finais = isolados_selecionados[:n_isolados]
        exercicios_finais = compostos_finais + isolados_finais
        total_desejado = n_compostos + n_isolados
        if len(exercicios_finais) < total_desejado:
            faltantes = total_desejado - len(exercicios_finais)
            if len(isolados_finais) < n_isolados and len(compostos_selecionados) > len(compostos_finais):
                extras = [ex for ex in compostos_selecionados if ex not in exercicios_finais][:faltantes]
                exercicios_finais.extend(extras);
                faltantes -= len(extras)
            if faltantes > 0 and len(compostos_finais) < n_compostos and len(isolados_selecionados) > len(
                    isolados_finais):
                extras = [ex for ex in isolados_selecionados if ex not in exercicios_finais][:faltantes]
                exercicios_finais.extend(extras)

        for ex in exercicios_finais:
            # Usa .split('-')[0] para pegar o menor número de séries (melhor para iniciantes)
            num_series = series_base.split('-')[0]
            exercicios_selecionados.append(
                {'Exercício': ex, 'Séries': num_series, 'Repetições': reps_base, 'Descanso': descanso_base})
        return exercicios_selecionados

    # Lógica de divisão do plano (permanece a mesma)
    plano = {}
    if dias <= 2:
        plano['Treino A: Corpo Inteiro'] = selecionar_exercicios(['Peito', 'Costas', 'Pernas', 'Ombros'], 3, 1)
        plano['Treino B: Corpo Inteiro'] = selecionar_exercicios(['Pernas', 'Costas', 'Peito', 'Bíceps', 'Tríceps'], 3,
                                                                 2)
    elif dias == 3:
        if sexo == 'Feminino':
            plano['Treino A: Superiores'] = selecionar_exercicios(['Peito', 'Costas', 'Ombros'], 2, 2)
            plano['Treino B: Inferiores (Foco Quad/Glúteo)'] = selecionar_exercicios(['Pernas'], 2, 3)
            plano['Treino C: Inferiores (Foco Post/Glúteo)'] = selecionar_exercicios(['Pernas'], 2, 3,
                                                                                     excluir=[ex['Exercício'] for ex in
                                                                                              plano[
                                                                                                  'Treino B: Inferiores (Foco Quad/Glúteo)']])
        else:
            plano['Treino A: Superiores (Push)'] = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 2, 3)
            plano['Treino B: Inferiores'] = selecionar_exercicios(['Pernas'], 2, 3)
            plano['Treino C: Superiores (Pull)'] = selecionar_exercicios(['Costas', 'Bíceps'], 2, 2)
    elif dias == 4:
        if sexo == 'Feminino':
            upper_a_ex = selecionar_exercicios(['Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps'], 3, 2)
            lower_a_ex = selecionar_exercicios(['Pernas'], 3, 2)
            plano['Treino A: Superiores A'] = upper_a_ex
            plano['Treino B: Inferiores A'] = lower_a_ex
            plano['Treino C: Superiores B'] = selecionar_exercicios(['Costas', 'Peito', 'Ombros', 'Bíceps', 'Tríceps'],
                                                                    3, 2,
                                                                    excluir=[ex['Exercício'] for ex in upper_a_ex])
            plano['Treino D: Inferiores B'] = selecionar_exercicios(['Pernas'], 2, 3,
                                                                    excluir=[ex['Exercício'] for ex in lower_a_ex])
        else:
            plano['Treino A: Superiores (Foco Peito/Costas)'] = selecionar_exercicios(['Peito', 'Costas', 'Bíceps'], 3,
                                                                                      2)
            plano['Treino B: Inferiores (Foco Quadríceps)'] = selecionar_exercicios(['Pernas'], 2, 3)
            plano['Treino C: Superiores (Foco Ombros/Braços)'] = selecionar_exercicios(['Ombros', 'Tríceps', 'Bíceps'],
                                                                                       2, 3)
            plano['Treino D: Inferiores (Foco Posterior/Glúteos)'] = selecionar_exercicios(['Pernas'], 2, 3)
    elif dias >= 5:
        if nivel_usuario == 'Iniciante':
            upper_a_ex = selecionar_exercicios(['Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps'], 2, 2)
            lower_a_ex = selecionar_exercicios(['Pernas'], 2, 2)
            upper_b_ex = selecionar_exercicios(['Costas', 'Peito', 'Ombros', 'Bíceps', 'Tríceps'], 2, 2,
                                               excluir=[ex['Exercício'] for ex in upper_a_ex])
            lower_b_ex = selecionar_exercicios(['Pernas'], 2, 2, excluir=[ex['Exercício'] for ex in lower_a_ex])
            plano['Dia 1: Superiores A'] = upper_a_ex
            plano['Dia 2: Inferiores A'] = lower_a_ex
            plano['Dia 3: Superiores B'] = upper_b_ex
            plano['Dia 4: Inferiores B'] = lower_b_ex
            plano['Dia 5: Superiores A'] = upper_a_ex
        else:  # Intermediário/Avançado
            if sexo == 'Feminino':
                plano['Treino A: Inferiores (Quadríceps)'] = selecionar_exercicios(['Pernas'], 2, 3)
                plano['Treino B: Superiores (Push)'] = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 2, 2)
                plano['Treino C: Inferiores (Posterior/Glúteos)'] = selecionar_exercicios(['Pernas'], 2, 3)
                plano['Treino D: Superiores (Pull)'] = selecionar_exercicios(['Costas', 'Bíceps'], 3, 1)
                plano['Treino E: Glúteos & Core'] = selecionar_exercicios(['Pernas', 'Core'], 1, 3)
                lista_c = plano['Treino C: Inferiores (Posterior/Glúteos)']
                lista_e = plano['Treino E: Glúteos & Core']
                if 'Elevação Pélvica' not in [ex['Exercício'] for ex in lista_c + lista_e]:
                    if len(lista_c) < 6:
                        lista_c.append({'Exercício': 'Elevação Pélvica', 'Séries': series_base.split('-')[0],
                                        'Repetições': reps_base, 'Descanso': descanso_base})
                    elif len(lista_e) < 5:
                        lista_e.append({'Exercício': 'Elevação Pélvica', 'Séries': series_base.split('-')[0],
                                        'Repetições': reps_base, 'Descanso': descanso_base})
            else:  # Masculino Intermediário/Avançado
                plano['Treino A: Peito'] = selecionar_exercicios(['Peito'], 2, 2)
                plano['Treino B: Costas'] = selecionar_exercicios(['Costas'], 4, 0)
                plano['Treino C: Pernas'] = selecionar_exercicios(['Pernas'], 2, 3)
                plano['Treino D: Ombros'] = selecionar_exercicios(['Ombros'], 2, 2)
                plano['Treino E: Braços & Core'] = selecionar_exercicios(['Bíceps', 'Tríceps', 'Core'], 0, 4)

    plano_final = {}
    for nome, exercicios_lista in plano.items():
        plano_final[nome] = exercicios_lista if exercicios_lista else []
    return plano_final


# ---------------------------
# Pages
# ---------------------------
def render_auth():
    show_logo_center()
    st.markdown("---")
    tab_login, tab_cad = st.tabs(["🔑 Login", "📝 Cadastro"])
    with tab_login:
        with st.form("form_login"):
            username = st.text_input("E-mail ou 'demo'")
            senha = st.text_input("Senha", type='password')
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.form_submit_button("👁️ Modo Demo"):
                    ok, msg = verificar_credenciais_firebase('demo', 'demo123')
                    if ok:
                        st.success(msg); st.rerun()
                    else:
                        st.error(msg)
            if st.form_submit_button("Entrar"):
                if not username or not senha:
                    st.error("Preencha username e senha.")
                else:
                    ok, msg = verificar_credenciais_firebase(username.strip(), senha)
                    if ok:
                        st.success(msg); st.rerun()
                    else:
                        st.error(msg)
    with tab_cad:
        with st.form("form_cadastro"):
            nome = st.text_input("Nome completo")
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type='password')
            senha_conf = st.text_input("Confirmar senha", type='password')
            termos = st.checkbox("Aceito os Termos de Uso")
            if st.form_submit_button("Criar Conta"):
                if not nome or len(nome.strip()) < 3:
                    st.error("Nome mínimo 3 caracteres.")
                elif not valid_email(email):
                    st.error("E-mail inválido.")
                elif len(senha) < 6:
                    st.error("Senha mínimo 6 caracteres.")
                elif senha != senha_conf:
                    st.error("Senhas não coincidem.")
                elif not termos:
                    st.error("Aceite os termos.")
                else:
                    ok, msg = criar_usuario_firebase(email.strip(), senha, nome.strip())
                    if ok:
                        st.success(msg); st.info("Faça login agora.")
                    else:
                        st.error(msg)
    st.stop()


def render_main():
    if st.session_state.get('workout_in_progress', False):
        render_workout_session()
        st.stop()
    check_notifications_on_open()
    st.sidebar.title("🏋️ FitPro")
    st.sidebar.write(f"👤 {st.session_state.get('usuario_logado')}")
    if st.sidebar.button("🚪 Sair"):
        uid = st.session_state.get('user_uid')
        if uid:
            salvar_dados_usuario_firebase(uid)
        del cookies['user_uid']
        keys = list(st.session_state.keys())
        for k in keys:
            if k != 'db': del st.session_state[k]
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.subheader("Configurações")
    theme = st.sidebar.selectbox("Tema", ["light", "dark"],
                                 index=0 if st.session_state['settings'].get('theme', 'light') == 'light' else 1)
    st.session_state['settings']['theme'] = theme
    notify_on_open = st.sidebar.checkbox("Notificações ao abrir",
                                         value=st.session_state['settings'].get('notify_on_login', True))
    st.session_state['settings']['notify_on_login'] = notify_on_open
    st.sidebar.checkbox("Modo offline (cache)", value=st.session_state.get('offline_mode', False), key='offline_mode')
    if st.session_state.get('role') == 'admin':
        st.sidebar.success("👑 Admin")
        if st.sidebar.button("Painel Admin"):
            st.session_state['page'] = 'Admin'
    if st.session_state.get('notificacoes'):
        for n in st.session_state['notificacoes']:
            if n['tipo'] == 'conquista':
                st.balloons()
                st.success(n['msg'])
            else:
                try:
                    st.toast(n['msg'])
                except Exception:
                    st.info(n['msg'])
    pages = ["Dashboard", "Rede Social", "Buscar Usuários", "Questionário", "Meu Treino", "Registrar Treino",
             "Progresso", "Fotos", "Comparar Fotos", "Medidas", "Planejamento Semanal", "Metas", "Nutrição", "Busca",
             "Export/Backup"]
    if st.session_state.get('role') == 'admin':
        pages.append("Admin")
    page = st.selectbox("Navegação", pages)
    page_map = {
        "Dashboard": render_dashboard, "Rede Social": render_rede_social, "Buscar Usuários": render_buscar_usuarios,
        "Questionário": render_questionario, "Meu Treino": render_meu_treino,
        "Registrar Treino": render_registrar_treino,
        "Progresso": render_progresso, "Fotos": render_fotos, "Comparar Fotos": render_comparar_fotos,
        "Medidas": render_medidas,
        "Planejamento Semanal": render_planner, "Metas": render_metas, "Nutrição": render_nutricao,
        "Busca": render_busca,
        "Export/Backup": render_export_backup, "Admin": render_admin_panel,
    }
    render_func = page_map.get(page, lambda: st.write("Página em desenvolvimento."))
    render_func()


# ---------------------------
# Page implementations
# ---------------------------
def render_workout_session():
    st.title("🔥 Treino em Andamento")
    plano_atual, idx_atual = st.session_state['current_workout_plan'], st.session_state['current_exercise_index']
    exercicio_atual = plano_atual[idx_atual]
    nome_exercicio, series_str = exercicio_atual['Exercício'], exercicio_atual['Séries']
    try:
        num_series = int(str(series_str).split('-')[0])
    except:
        num_series = 3

    # --- Barra de Progresso e Timer ---
    progresso = (idx_atual + 1) / len(plano_atual)
    col_prog, col_timer = st.columns(2)
    col_prog.progress(progresso, text=f"Exercício {idx_atual + 1} de {len(plano_atual)}")
    timer_placeholder = col_timer.empty()

    # --- Lógica do Timer Único ---
    is_resting = False
    if st.session_state.rest_timer_end:
        remaining = st.session_state.rest_timer_end - time.time()
        if remaining > 0:
            is_resting = True
            mins, secs = divmod(int(remaining), 60)
            timer_placeholder.metric("⏳ Descanso", f"{mins:02d}:{secs:02d}")
            time.sleep(1)
            st.rerun()
        else:
            st.session_state.rest_timer_end = None
            st.toast("💪 Descanso finalizado!")
            st.rerun()

    # --- Container do Exercício Atual (com vídeo) ---
    with st.container(border=True):
        col_video, col_details = st.columns([2, 3])
        # --- CORREÇÃO AQUI ---
        with col_video: # Substitua 'ith' e 'col_gif:' por esta linha, com a indentação correta
            # Chama a NOVA função de busca e usa st.video
            video_url = find_exercise_video_youtube(nome_exercicio)
            if video_url:
                st.video(video_url)
            else:
                st.text("Vídeo indisponível")
        # --- FIM DA CORREÇÃO ---
        with col_details:
            st.header(nome_exercicio)
            st.markdown(
                f"**Séries:** `{exercicio_atual['Séries']}` | **Repetições:** `{exercicio_atual['Repetições']}`\n**Descanso:** `{exercicio_atual['Descanso']}`")
    for i in range(num_series):
        set_key = f"set_{idx_atual}_{i}"
        if set_key not in st.session_state: st.session_state[set_key] = {'completed': False, 'weight': 0.0, 'reps': 0}
        set_info = st.session_state[set_key]
        cols = st.columns([1, 2, 2, 1])
        disable_checkbox = is_resting and not set_info['completed']  # Desabilita checkbox se estiver em descanso
        completed = cols[0].checkbox(f"Série {i + 1}", value=set_info['completed'], key=f"check_{set_key}",
                                     disabled=disable_checkbox)
        if completed != set_info['completed']:
            set_info['completed'] = completed
            if completed:
                if is_resting:
                    st.warning("Termine seu descanso antes de marcar a próxima série!")
                    set_info['completed'] = False
                else:
                    descanso_str = exercicio_atual.get('Descanso', '60s')
                    try:
                        rest_seconds = int(re.search(r'\d+', descanso_str).group())
                    except:
                        rest_seconds = 60
                    st.session_state.rest_timer_end = time.time() + rest_seconds
                    st.session_state.workout_log.append(
                        {'data': date.today().isoformat(), 'exercicio': nome_exercicio, 'series': i + 1,
                         'peso': set_info['weight'], 'reps': set_info['reps'], 'timestamp': iso_now()})
            elif set_key in st.session_state['set_timers']:
                del st.session_state['set_timers'][
                    set_key]  # Este else foi mantido por segurança, embora a lógica do timer esteja no rest_timer_end
            st.rerun()
        if not set_info['completed']:
            set_info['weight'] = cols[1].number_input("Peso (kg)", key=f"weight_{set_key}",
                                                      value=float(set_info['weight']), format="%.1f",
                                                      disabled=is_resting)
            set_info['reps'] = cols[2].number_input("Reps", key=f"reps_{set_key}", value=int(set_info['reps']),
                                                    disabled=is_resting)
        else:
            cols[1].write(f"Peso: **{set_info['weight']} kg**");
            cols[2].write(f"Reps: **{set_info['reps']}**")

    st.markdown("---")
    all_sets_done = all(
        st.session_state.get(f"set_{idx_atual}_{i}", {}).get('completed', False) for i in range(num_series))
    nav_cols = st.columns([1, 1, 1])
    if all_sets_done:
        if idx_atual < len(plano_atual) - 1:
            if nav_cols[1].button("Próximo Exercício →", use_container_width=True, type="primary"):
                st.session_state['current_exercise_index'] += 1;
                st.rerun()
        else:
            if nav_cols[1].button("✅ Finalizar Treino", use_container_width=True, type="primary"):
                hist = st.session_state.get('historico_treinos', []);
                hist.extend(st.session_state.workout_log);
                st.session_state['historico_treinos'] = hist
                freq = st.session_state.get('frequencia', []);
                today = date.today()
                if today not in freq: freq.append(today); st.session_state['frequencia'] = freq
                salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
                st.session_state['workout_in_progress'] = False;
                st.balloons();
                st.success("Treino finalizado!");
                time.sleep(2);
                st.rerun()
    if nav_cols[2].button("❌ Desistir do Treino", use_container_width=True):
        st.session_state['workout_in_progress'] = False;
        st.warning("Treino cancelado.");
        time.sleep(1);
        st.rerun()


def render_rede_social():
    st.title("🌐 Feed Social")
    st.markdown("---")
    with st.expander("💬 Criar nova publicação"):
        with st.form("form_novo_post", clear_on_submit=True):
            comentario = st.text_area("O que você está pensando?",
                                      placeholder="Compartilhe seu progresso, uma dica ou uma foto do seu treino!")
            foto_post = st.file_uploader("Adicionar uma foto", type=['png', 'jpg', 'jpeg'])
            submitted = st.form_submit_button("Publicar")
            if submitted:
                user_uid = st.session_state.get('user_uid')
                username = st.session_state.get('usuario_logado')
                img_b64 = None
                if foto_post:
                    try:
                        img = Image.open(foto_post).convert('RGB')
                        img.thumbnail((800, 800))
                        img_b64 = b64_from_pil(img)
                    except Exception as e:
                        st.error(f"Erro ao processar a imagem: {e}")
                with st.spinner("Publicando..."):
                    sucesso = salvar_post_firebase(user_uid, username, comentario, img_b64)
                    if sucesso:
                        st.success("Publicação criada com sucesso!"); st.rerun()
                    else:
                        st.error("Não foi possível criar a publicação.")
    st.markdown("---")
    st.subheader("Seu Feed")
    user_uid = st.session_state.get('user_uid')
    posts = carregar_feed_firebase(user_uid)
    if not posts:
        st.info(
            "Seu feed está vazio. Siga outros atletas na página 'Buscar Usuários' para ver as publicações deles aqui!")
        return
    for post in posts:
        post_id = post.get('id')
        username = post.get('username', 'Usuário Anônimo')
        timestamp = post.get('timestamp')
        data_post = timestamp.strftime("%d/%m/%Y às %H:%M") if isinstance(timestamp, datetime) else "algum tempo atrás"
        with st.container(border=True):
            st.markdown(f"**👤 {username}** · *{data_post}*")
            if post.get('text_content'): st.write(post['text_content'])
            if post.get('image_b64'):
                try:
                    st.image(base64.b64decode(post['image_b64']))
                except Exception:
                    st.warning("Não foi possível carregar a imagem deste post.")
            like_count, comment_count = post.get('like_count', 0), post.get('comment_count', 0)
            col1, col2, _ = st.columns([1, 1, 5])
            with col1:
                if st.button(f"❤️ Curtir ({like_count})", key=f"like_{post_id}"):
                    curtir_post(post_id, st.session_state.get('user_uid'));
                    st.rerun()
            with col2:
                st.write(f"💬 Comentários ({comment_count})")
            with st.expander("Ver e adicionar comentários"):
                comentarios = carregar_comentarios(post_id)
                if comentarios:
                    for comment in comentarios:
                        st.markdown(f"> **{comment.get('username', 'Usuário')}:** {comment.get('text', '')}")
                else:
                    st.write("Nenhum comentário ainda.")
                comment_text = st.text_input("Escreva um comentário...", key=f"comment_input_{post_id}",
                                             label_visibility="collapsed")
                if st.button("Enviar", key=f"comment_btn_{post_id}"):
                    if comment_text:
                        sucesso = comentar_post(post_id, st.session_state.get('user_uid'),
                                                st.session_state.get('usuario_logado'), comment_text)
                        if sucesso: st.session_state[f"comment_input_{post_id}"] = ""; st.rerun()
                    else:
                        st.warning("O comentário não pode estar vazio.")


def render_buscar_usuarios():
    st.title("🔎 Buscar Usuários")
    st.info("Encontre outros atletas e comece a segui-los para ver suas publicações no seu feed.")
    current_user_uid = st.session_state.get('user_uid')
    all_users = get_all_users()
    following_list = get_following_list(current_user_uid)
    if not all_users:
        st.warning("Nenhum usuário encontrado.")
        return
    for user in all_users:
        user_id, username = user['id'], user['username']
        if user_id == current_user_uid: continue
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.subheader(username)
            with col2:
                is_following = user_id in following_list
                if is_following:
                    if st.button("Deixar de Seguir", key=f"unfollow_{user_id}", use_container_width=True):
                        unfollow_user(current_user_uid, user_id);
                        st.success(f"Você deixou de seguir {username}.");
                        st.rerun()
                else:
                    if st.button("Seguir", key=f"follow_{user_id}", type="primary", use_container_width=True):
                        follow_user(current_user_uid, user_id);
                        st.success(f"Você está seguindo {username}!");
                        st.rerun()


def render_dashboard():
    st.title("📊 Dashboard")
    show_logo_center()
    dados = st.session_state.get('dados_usuario') or {}
    num_treinos = len(set(st.session_state.get('frequencia', [])))
    st.metric("Treinos Completos", num_treinos)
    if num_treinos > 0:
        info = verificar_periodizacao(num_treinos)
        fase = info['fase_atual']
        st.markdown(f"""<div style='padding:20px;border-radius:12px;background:linear-gradient(90deg,{fase['cor']},#ffffff);color:#111;'>
            <h3>🎯 Fase Atual: {fase['nome']} | Ciclo {info['numero_ciclo']}</h3>
            <p>{fase['reps']} reps · {fase['series']} séries · Descanso {fase['descanso']}</p></div>""",
                    unsafe_allow_html=True)
    if st.session_state.get('medidas'):
        dfm = pd.DataFrame(st.session_state['medidas'])
        dfm['data'] = pd.to_datetime(dfm['data'])
        fig = px.line(dfm, x='data', y='valor', color='tipo', markers=True, title='Evolução de Medidas')
        st.plotly_chart(fig, use_container_width=True)
    st.subheader("📅 Calendário de Treinos (últimos 30 dias)")
    if st.session_state.get('frequencia'):
        hoje = datetime.now().date()
        ult30 = [hoje - timedelta(days=i) for i in range(30)]
        treinos_30 = set(st.session_state['frequencia'])
        df_cal = pd.DataFrame({'data': ult30})
        df_cal['treinou'] = df_cal['data'].isin(treinos_30).astype(int)
        df_cal['data'] = pd.to_datetime(df_cal['data'])
        df_cal['weekday'] = df_cal['data'].dt.day_name()
        df_cal['week'] = df_cal['data'].dt.isocalendar().week
        try:
            pivot = df_cal.pivot(index='week', columns='weekday', values='treinou').fillna(0)
            fig = px.imshow(pivot, labels=dict(x='Dia', y='Semana', color='Treinou'), text_auto=True)
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            st.table(df_cal)
    else:
        st.info("Registre treinos para ver o calendário.")


def render_questionario():
    st.title("🏋️ Perfil do Atleta")
    st.markdown("Responda ao formulário para gerarmos um plano de treino **exclusivo para você**.")
    dados = st.session_state.get('dados_usuario') or {}
    with st.form("form_q"):
        col1, col2 = st.columns(2)
        with col1:
            nome = st.text_input("Nome completo", value=dados.get('nome', ''))
            idade = st.number_input("Idade", 12, 100, value=dados.get('idade', 25))
            peso = st.number_input("Peso (kg)", 30.0, 200.0, value=dados.get('peso', 70.0), step=0.1)
            altura = st.number_input("Altura (cm)", 100.0, 250.0, value=dados.get('altura', 170.0), step=0.1)
            # [NOVO] Campo Sexo
            sexo = st.selectbox("Sexo", ["Masculino", "Feminino"], index=0 if dados.get('sexo', 'Masculino') == 'Masculino' else 1)

        with col2:
            nivel = st.selectbox("Qual seu nível de experiência?", ["Iniciante", "Intermediário/Avançado"],
                                 index=0 if dados.get('nivel') == 'Iniciante' else 1)
            objetivo = st.selectbox("Qual seu objetivo principal?", ["Hipertrofia", "Emagrecimento", "Condicionamento"],
                                      index=["Hipertrofia", "Emagrecimento", "Condicionamento"].index(
                                          dados.get('objetivo', 'Hipertrofia')))
            dias = st.slider("Quantos dias por semana pode treinar?", 2, 6, value=dados.get('dias_semana', 3))

        restricoes = st.multiselect("Possui alguma dor ou restrição nas seguintes áreas?",
                                    ["Lombar", "Joelhos", "Ombros", "Cotovelos", "Punhos"],
                                    default=dados.get('restricoes', []))

        if st.form_submit_button("Salvar Perfil e Gerar Treino"):
            # [MODIFICADO] Adiciona 'sexo' aos novos dados
            novos_dados = {'nome': nome, 'idade': idade, 'peso': peso, 'altura': altura, 'sexo': sexo, # <- Adicionado
                           'nivel': nivel, 'objetivo': objetivo, 'dias_semana': dias, 'restricoes': restricoes,
                           'data_cadastro': iso_now()}
            st.session_state['dados_usuario'] = novos_dados
            hp = st.session_state.get('historico_peso', [])
            if not hp or hp[-1].get('peso') != peso:
                hp.append({'data': iso_now(), 'peso': peso})
                st.session_state['historico_peso'] = hp
            with st.spinner("🤖 Criando seu plano de treino personalizado..."):
                st.session_state['plano_treino'] = gerar_plano_personalizado(novos_dados)
                time.sleep(1)
            uid = st.session_state.get('user_uid')
            if uid:
                salvar_dados_usuario_firebase(uid)
            st.success("Perfil salvo e plano de treino personalizado gerado com sucesso!")
            st.info("Acesse a página 'Meu Treino' para visualizar.")


def render_meu_treino():
    st.title("💪 Meu Treino")
    plano = st.session_state.get('plano_treino')

    plano_vazio = True
    if plano and isinstance(plano, dict):
        for nome_treino, treino_data in plano.items():
            if isinstance(treino_data, pd.DataFrame) and not treino_data.empty:
                plano_vazio = False; break
            elif isinstance(treino_data, list) and treino_data and all(isinstance(item, dict) for item in treino_data):
                plano_vazio = False; break

    if not plano or plano_vazio:
        st.info("Você ainda não tem um plano de treino. Vá para a página 'Questionário' para gerar o seu primeiro!")
        return

    dados = st.session_state.get('dados_usuario') or {}
    st.info(
        f"Este plano foi criado para um atleta **{dados.get('nivel', '')}** treinando **{dados.get('dias_semana', '')}** dias por semana com foco em **{dados.get('objetivo', '')}**.")
    st.markdown("---")

    for nome_treino, treino_data in plano.items():
        if isinstance(treino_data, pd.DataFrame):
            df_treino = treino_data
        elif isinstance(treino_data, list):
            df_treino = pd.DataFrame(treino_data)
        else:
            df_treino = pd.DataFrame()

        if df_treino.empty: continue

        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader(nome_treino)
            st.caption(f"{len(df_treino)} exercícios")
        with col2:
            # Botão para iniciar o modo interativo (existente)
            if st.button("▶️ Iniciar Treino", key=f"start_{nome_treino}", use_container_width=True, type="primary"):
                st.session_state.update(
                    {'workout_in_progress': True, 'current_workout_plan': df_treino.to_dict('records'),
                     'current_exercise_index': 0, 'workout_log': [], 'rest_timer_end': None})
                st.rerun()

            # --- NOVO BOTÃO ADICIONADO ABAIXO ---
            if st.button("✅ Marcar Concluído", key=f"quick_complete_{nome_treino}", use_container_width=True):
                hoje = date.today()  # Pega a data atual
                frequencia_atual = st.session_state.get('frequencia', [])

                # Verifica se o treino de hoje já foi contabilizado
                if hoje not in frequencia_atual:
                    frequencia_atual.append(hoje)  # Adiciona data à lista
                    st.session_state['frequencia'] = frequencia_atual  # Atualiza o estado da sessão

                    # Salva a alteração no Firebase
                    salvar_dados_usuario_firebase(st.session_state.get('user_uid'))

                    st.toast(f"Ótimo! Treino '{nome_treino}' contabilizado para hoje.")

                    # Opcional: Forçar re-run para atualizar contadores no dashboard imediatamente
                    # time.sleep(1) # Pequena pausa para o toast ser visível
                    # st.rerun()
                else:
                    # Informa que já foi contabilizado
                    st.toast("O treino de hoje já foi contabilizado!")
            st.caption("Marca o dia como treinado.")  # Legenda explicativa
            # --- FIM DO NOVO BOTÃO ---

        # O restante do código (expanders, vídeos, etc.) permanece igual
        for index, row in df_treino.iterrows():
            exercicio, series, repeticoes, descanso = row['Exercício'], row['Séries'], row['Repetições'], row[
                'Descanso']
            with st.expander(f"**{exercicio}** | {series} Séries x {repeticoes} Reps"):
                col_media, col_instr = st.columns([1, 2])
                with col_media:
                    video_url = find_exercise_video_youtube(exercicio)
                    if video_url:
                        st.link_button("🎥 Assistir Execução", video_url)
                        st.caption(f"Abre o vídeo de {exercicio} no YouTube")
                    else:
                        st.info("Vídeo de execução indisponível.")
                with col_instr:
                    st.markdown("##### 📋 **Instruções**")
                    st.markdown(
                        f"- **Séries:** `{series}`\n- **Repetições:** `{repeticoes}`\n- **Descanso:** `{descanso}`")
                    st.markdown("---")
                    ex_data = EXERCICIOS_DB.get(exercicio, {})
                    grupo_muscular = ex_data.get('grupo', 'N/A')
                    equipamento = ex_data.get('equipamento', 'N/A')
                    descricao_exercicio = ex_data.get('descricao')
                    st.write(f"**Grupo Muscular:** {grupo_muscular}")
                    st.write(f"**Equipamento:** {equipamento}")
                    if descricao_exercicio:
                        st.markdown("---")
                        st.markdown(f"**📝 Como Fazer:**\n{descricao_exercicio}")
                    st.markdown(" ")
                    st.button("🔄 Trocar Exercício", key=f"swap_{nome_treino}_{index}", on_click=trocar_exercicio,
                              args=(nome_treino, index, exercicio), use_container_width=True)
        st.markdown("---")


def render_registrar_treino():
    st.title("📝 Registrar Treino")
    with st.form("f_registrar"):
        data = st.date_input("Data", datetime.now().date())
        tipos = list(st.session_state.get('plano_treino', {}).keys()) + ["Cardio", "Outro"] if st.session_state.get(
            'plano_treino') else ["Cardio", "Outro"]
        tipo = st.selectbox("Tipo", tipos)
        exercicio = st.selectbox("Exercício", [""] + sorted(list(EXERCICIOS_DB.keys())))
        c1, c2, c3 = st.columns(3)
        with c1:
            series = st.number_input("Séries", 1, 12, 3)
        with c2:
            reps = st.number_input("Repetições", 1, 50, 10)
        with c3:
            peso = st.number_input("Peso (kg)", 0.0, 500.0, 0.0, 0.5)
        obs = st.text_area("Observações")
        if st.form_submit_button("Registrar"):
            if not exercicio:
                st.error("Escolha um exercício.")
            else:
                novo = {'data': data.isoformat(), 'tipo': tipo, 'exercicio': exercicio, 'series': int(series),
                        'reps': int(reps), 'peso': float(peso), 'volume': int(series) * int(reps) * float(peso),
                        'observacoes': obs, 'timestamp': iso_now()}
                hist = st.session_state.get('historico_treinos', [])
                hist.append(novo)
                st.session_state['historico_treinos'] = hist
                freq = st.session_state.get('frequencia', [])
                if data not in freq:
                    freq.append(data)
                    st.session_state['frequencia'] = freq
                uid = st.session_state.get('user_uid')
                if uid: salvar_dados_usuario_firebase(uid)
                st.success("✅ Treino registrado.")
                with st.form("form_feedback_quick"):
                    st.subheader("Feedback rápido")
                    nota = st.slider("Dificuldade (1-5)", 1, 5, 3)
                    dor = st.checkbox("Teve dor/desconforto")
                    comentarios = st.text_area("Comentários (opcional)")
                    if st.form_submit_button("Enviar feedback"):
                        st.session_state['feedbacks'].append(
                            {'exercicio': exercicio, 'nota': nota, 'dor': dor, 'comentarios': comentarios,
                             'data': data.isoformat()})
                        if uid: salvar_dados_usuario_firebase(uid)
                        st.success("Obrigado pelo feedback!")


def render_progresso():
    st.title("📈 Progresso")
    hist = st.session_state.get('historico_treinos', [])
    if not hist: st.info("Registre treinos para ver gráficos."); return
    df = pd.DataFrame(hist)
    df['data'] = pd.to_datetime(df['data'])
    vol = df.groupby(df['data'].dt.date)['volume'].sum().reset_index()
    fig = px.line(vol, x='data', y='volume', title='Volume por dia', markers=True)
    st.plotly_chart(fig, use_container_width=True)
    vol['rolling'] = vol['volume'].rolling(7, min_periods=1).mean()
    if len(vol['rolling']) >= 8:
        last, prev = vol['rolling'].iloc[-1], vol['rolling'].iloc[-8]
        if prev > 0 and abs(last - prev) / prev < 0.05:
            st.warning("Possível platô detectado (variação <5% nas últimas semanas).")


def render_fotos():
    st.title("📸 Fotos de Progresso")

    # Usar 'expanded=True' para deixar aberto por padrão, facilitando o acesso
    with st.expander("➕ Adicionar Nova Foto", expanded=True):
        col_upload, col_details = st.columns([1, 1]) # Dividir em duas colunas de tamanho igual

        with col_upload:
            st.markdown("##### 1. Selecione a Imagem")
            uploaded = st.file_uploader(
                "Arraste e solte ou clique para buscar", # Label mais interativo
                type=['png', 'jpg', 'jpeg'],
                label_visibility="collapsed" # Oculta o label padrão, já temos o markdown acima
            )
            st.caption("Formatos: PNG, JPG, JPEG. Limite: 200MB.")

        # A coluna de detalhes só mostra conteúdo se uma imagem foi carregada
        with col_details:
            if uploaded is not None:
                st.markdown("##### 2. Detalhes e Salvar")
                try:
                    img = Image.open(uploaded).convert('RGB')
                    # Mostrar preview com largura da coluna
                    st.image(img, caption='Preview da Imagem', use_column_width=True)

                    data_foto = st.date_input("🗓️ Data da foto", datetime.now().date())

                    # Tenta pegar o último peso registrado como padrão
                    default_weight = st.session_state.get('dados_usuario', {}).get('peso', 70.0)
                    if st.session_state.get('historico_peso'):
                        try: default_weight = st.session_state['historico_peso'][-1]['peso']
                        except: pass # Ignora erro se formato inesperado

                    peso_foto = st.number_input("⚖️ Peso (kg) neste dia", min_value=20.0, value=float(default_weight), step=0.1)
                    nota = st.text_area("📝 Notas / Observações (opcional)")

                    if st.button("💾 Salvar foto", use_container_width=True, type="primary"):
                        with st.spinner("Processando e salvando..."):
                            b64 = b64_from_pil(img)
                            fotos = st.session_state.get('fotos_progresso', [])
                            fotos.append({'data': data_foto.isoformat(), 'peso': float(peso_foto), 'imagem': b64, 'nota': nota, 'timestamp': iso_now()})
                            # Ordenação agora feita na exibição da galeria
                            st.session_state['fotos_progresso'] = fotos
                            uid = st.session_state.get('user_uid')
                            if uid: salvar_dados_usuario_firebase(uid)
                            st.success("Foto salva com sucesso!")
                            time.sleep(1)
                            st.rerun() # Limpa o uploader e atualiza a galeria
                except Exception as e:
                    st.error(f"Erro ao processar imagem: {e}")
            else:
                # Mensagem enquanto nenhuma imagem foi selecionada
                st.info("⬅️ Selecione um arquivo para ver o preview e adicionar detalhes.")

    st.markdown("---") # Separador antes da galeria

    # --- Galeria de Fotos (restante da função) ---
    st.subheader("🖼️ Galeria de Progresso")
    # Ordena as fotos pela data mais recente aqui, antes de exibir
    fotos = sorted(st.session_state.get('fotos_progresso', []), key=lambda x: x.get('data', ''), reverse=True)

    if not fotos:
        st.info("Nenhuma foto adicionada ainda.")
        return # Adiciona return para clareza

    # Lógica de exibição e exclusão da galeria (permanece igual)
    for i, f in enumerate(fotos):
        # Proteção extra caso 'imagem' não exista ou esteja corrompida
        if 'imagem' not in f or not f['imagem']: continue

        c1, c2, c3 = st.columns([1, 3, 1])
        with c1:
            try:
                # Usar um tamanho fixo pode ser melhor que width=140 para consistência
                st.image(base64.b64decode(f['imagem']), width=150)
            except Exception:
                st.error("Erro ao carregar imagem") # Mensagem mais clara
        with c2:
            data_str = f.get('data', 'Data N/A')
            peso_str = f"{f.get('peso', '?'):.1f}kg" if f.get('peso') else ""
            st.write(f"📅 **{data_str}** ⚖️ **{peso_str}**")
            if f.get('nota'):
                st.caption(f"📝 {f.get('nota')}")
        with c3:
            if st.button("🗑️ Excluir", key=f"del_{i}", use_container_width=True):
                confirm_delete_photo_dialog(i, st.session_state.get('user_uid'))

    # Lógica de confirmação de exclusão (permanece igual)
    if st.session_state.get('confirm_excluir_foto'):
        st.warning("Deseja realmente excluir esta foto?")
        ca, cb = st.columns(2)
        with ca:
            if st.button("❌ Cancelar", key="cancel_delete"): # Adiciona key para evitar conflitos
                st.session_state['confirm_excluir_foto'] = False
                st.session_state['foto_a_excluir'] = None
                st.rerun()
        with cb:
            if st.button("✅ Confirmar exclusão", key="confirm_delete"): # Adiciona key
                idx = st.session_state.get('foto_a_excluir')
                # Recarrega a lista FOTOS DENTRO DA CONFIRMAÇÃO para garantir índice correto
                fotos_atual = sorted(st.session_state.get('fotos_progresso', []), key=lambda x: x.get('data', ''), reverse=True)
                if idx is not None and idx < len(fotos_atual):
                    foto_para_excluir = fotos_atual.pop(idx) # Remove da lista ordenada
                    # Encontra o item correspondente na lista original do session_state para remover
                    lista_original = st.session_state['fotos_progresso']
                    item_original_idx = -1
                    try:
                       # Tenta encontrar pelo timestamp ou imagem como identificador único
                       ts_para_excluir = foto_para_excluir.get('timestamp')
                       if ts_para_excluir:
                           item_original_idx = next(i for i, item in enumerate(lista_original) if item.get('timestamp') == ts_para_excluir)
                       else: # Fallback pela imagem se timestamp não existir
                           img_para_excluir = foto_para_excluir.get('imagem')
                           item_original_idx = next(i for i, item in enumerate(lista_original) if item.get('imagem') == img_para_excluir)
                    except (StopIteration, KeyError):
                        st.error("Erro ao encontrar foto original para excluir.")

                    if item_original_idx != -1:
                        del lista_original[item_original_idx]
                        st.session_state['fotos_progresso'] = lista_original # Salva a lista modificada
                        uid = st.session_state.get('user_uid')
                        if uid: salvar_dados_usuario_firebase(uid)
                        st.success("Foto excluída.")
                    else:
                         st.error("Não foi possível excluir a foto (item não encontrado).")

                else:
                     st.error("Índice inválido para exclusão.")

                st.session_state['confirm_excluir_foto'] = False
                st.session_state['foto_a_excluir'] = None
                st.rerun()


def render_comparar_fotos():
    st.title("🔍 Comparar Fotos")
    fotos = st.session_state.get('fotos_progresso', [])
    if len(fotos) < 2: st.info("Adicione pelo menos duas fotos para comparar."); return
    options = [f"{i} - {f['data']} - {f.get('peso')}kg" for i, f in enumerate(fotos)]
    sel = st.multiselect("Escolha duas fotos (antes, depois)", options, default=[options[-1], options[0]])
    if len(sel) != 2: st.info("Selecione exatamente duas fotos."); return
    idx1, idx2 = options.index(sel[0]), options.index(sel[1])
    img1, img2 = pil_from_b64(fotos[idx1]['imagem']), pil_from_b64(fotos[idx2]['imagem'])
    col1, col2 = st.columns(2)
    with col1:
        st.image(img1, caption=f"Antes: {fotos[idx1]['data']}")
    with col2:
        st.image(img2, caption=f"Depois: {fotos[idx2]['data']}")
    alpha = st.slider("Alpha (0=antes,1=depois)", 0.0, 1.0, 0.5)
    blended = overlay_blend(img1, img2, alpha)
    st.image(blended, caption=f"Blend (alpha={alpha})", use_column_width=True)
    st.json(compare_images_metric(img1, img2))


def render_medidas():
    st.title("📏 Medidas Corporais")

    # --- Formulário para adicionar nova medida ---
    # (Permanece igual)
    with st.form("form_med", clear_on_submit=True):
        tipo = st.selectbox("Tipo", ['Cintura', 'Quadril', 'Braço', 'Coxa', 'Peito'])
        valor = st.number_input("Valor (cm)", min_value=10.0, max_value=300.0, value=40.0, step=0.1)
        data_medida = st.date_input("Data", datetime.now().date())
        submitted = st.form_submit_button("Salvar medida")
        if submitted:
            medidas = st.session_state.get('medidas', [])
            nova_medida = {'tipo': tipo, 'valor': float(valor), 'data': data_medida.isoformat(), 'timestamp': iso_now()}
            medidas.append(nova_medida)
            st.session_state['medidas'] = medidas # Atualiza sem ordenar aqui
            uid = st.session_state.get('user_uid')
            if uid: salvar_dados_usuario_firebase(uid)
            st.success(f"Medida de {tipo} ({valor} cm) salva!")
            st.rerun()

    st.markdown("---")

    # --- Exibição das Últimas Medidas Registradas ---
    # (Permanece igual, com a lógica corrigida de sort/drop_duplicates)
    st.subheader("Últimas Medidas Registradas")
    medidas_salvas = st.session_state.get('medidas', [])
    if not medidas_salvas:
        st.info("Nenhuma medida registrada ainda. Use o formulário acima para adicionar.")
    else:
        latest_measurements = {}
        try:
            df_medidas = pd.DataFrame(medidas_salvas)
            df_medidas['data'] = pd.to_datetime(df_medidas['data'])
            if 'timestamp' not in df_medidas.columns: df_medidas['timestamp'] = pd.to_datetime(df_medidas['data'], errors='coerce').fillna(pd.Timestamp('1970-01-01'))
            else: df_medidas['timestamp'] = pd.to_datetime(df_medidas['timestamp'], errors='coerce').fillna(pd.Timestamp('1970-01-01'))
            df_medidas_sorted = df_medidas.sort_values(by=['data', 'timestamp'], ascending=[False, False])
            df_latest = df_medidas_sorted.drop_duplicates(subset='tipo', keep='first')
            latest_measurements = df_latest.set_index('tipo').to_dict('index')
        except Exception as e: st.error(f"Erro ao processar as medidas salvas: {e}")

        tipos_esperados = ['Cintura', 'Quadril', 'Braço', 'Coxa', 'Peito']
        cols = st.columns(len(tipos_esperados))
        for i, tipo_m in enumerate(tipos_esperados):
            with cols[i]:
                if tipo_m in latest_measurements:
                    medida = latest_measurements[tipo_m]; valor_m = medida['valor']; data_dt = medida['data']
                    data_m_str = data_dt.strftime('%d/%m/%Y') if pd.notnull(data_dt) else "Data inválida"
                    st.metric(label=f"{tipo_m}", value=f"{valor_m:.1f} cm", delta=f"Em {data_m_str}", delta_color="off")
                else: st.metric(label=tipo_m, value="N/A", delta="Não registrado", delta_color="off")

    st.markdown("---")

    # --- [REFORMATADO] Exibição de Indicadores de Referência (Saúde) ---
    st.subheader("📊 Indicadores de Referência (Saúde)") # Emoji adicionado
    dados_usuario = st.session_state.get('dados_usuario')

    if dados_usuario and 'altura' in dados_usuario and 'sexo' in dados_usuario:
        altura_cm = dados_usuario.get('altura', 0)
        sexo_usr = dados_usuario.get('sexo', 'Masculino')

        if altura_cm > 0:
            # 1. Relação Cintura-Altura (RCA)
            rca_ideal_max = altura_cm / 2
            st.markdown(f"🎯 **Relação Cintura-Altura (RCA):**")
            st.markdown(f"> Para **menor risco cardiovascular**, idealmente a circunferência da cintura deve ser **menor que `{rca_ideal_max:.1f} cm`** (metade da sua altura).")

            # 2. Circunferência Abdominal (Limites de Risco)
            st.markdown(f"⚠️ **Circunferência da Cintura (Risco Cardiovascular):**")
            if sexo_usr == 'Masculino':
                st.markdown("- Risco Aumentado: ≥ `94 cm`\n- Risco **Muito** Aumentado: ≥ `102 cm`")
            else: # Feminino
                st.markdown("- Risco Aumentado: ≥ `80 cm`\n- Risco **Muito** Aumentado: ≥ `88 cm`")
            st.caption("Valores de referência comuns. Consulte um profissional de saúde.")

        else:
            st.warning("Altura não encontrada no seu perfil. Preencha o questionário para ver as referências.")

        # 3. Relação Cintura-Quadril (RCQ)
        cintura_recente = latest_measurements.get('Cintura', {}).get('valor')
        quadril_recente = latest_measurements.get('Quadril', {}).get('valor')
        if cintura_recente and quadril_recente and quadril_recente > 0:
             rcq = cintura_recente / quadril_recente
             st.markdown("---") # Separador
             st.markdown(f"📉 **Relação Cintura-Quadril (RCQ) Atual:** `{rcq:.2f}`")
             if sexo_usr == 'Masculino':
                 risco_rcq = "**Alto** 🔴" if rcq >= 0.90 else "**Baixo/Moderado** ✅"
                 st.markdown(f"- Referência (Homens): Risco aumentado ≥ `0.90`. Seu risco atual: {risco_rcq}")
             else: # Feminino
                 risco_rcq = "**Alto** 🔴" if rcq >= 0.85 else "**Baixo/Moderado** ✅"
                 st.markdown(f"- Referência (Mulheres): Risco aumentado ≥ `0.85`. Seu risco atual: {risco_rcq}")
             st.caption("RCQ é outro indicador de risco cardiovascular e distribuição de gordura.")

    else:
        st.info("ℹ️ Preencha o questionário (altura e sexo) para visualizar indicadores de referência.")


def render_planner():
    st.title("🗓️ Planejamento Semanal Sugerido")
    dados_usuario = st.session_state.get('dados_usuario') or {}
    plano_treino = st.session_state.get('plano_treino')

    if not dados_usuario or not plano_treino:
        st.warning("Preencha o questionário para gerar seu plano e visualizar o planejamento.")
        return

    dias_semana_num = dados_usuario.get('dias_semana', 3)

    # Mapeamento de índice de dia da semana para nome
    dias_nomes = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

    # Obtém os índices dos dias sugeridos (0=Seg, 6=Dom)
    suggested_day_indices = suggest_days(dias_semana_num)
    suggested_day_names = [dias_nomes[i] for i in suggested_day_indices]

    st.info(
        f"Com base nos seus **{dias_semana_num} dias/semana**, sugerimos treinar em: **{', '.join(suggested_day_names)}**.")
    st.markdown("---")
    st.subheader("Próximos 7 Dias:")

    # Pega os nomes dos treinos (A, B, C...) em ordem
    nomes_treinos = list(plano_treino.keys())
    # Cria um 'ciclo' para repetir os nomes dos treinos (A, B, C, A, B, C...)
    ciclo_treinos = cycle(nomes_treinos)

    # Calcula as datas para os próximos 7 dias
    hoje = datetime.now().date()
    proximos_7_dias = [(hoje + timedelta(days=i)) for i in range(7)]

    # Cria 7 colunas para exibir os dias
    cols = st.columns(7)

    treino_counter = 0  # Contador para saber qual treino (A, B, C) usar

    for i, dia_data in enumerate(proximos_7_dias):
        dia_semana_idx = dia_data.weekday()  # 0 para Segunda, 6 para Domingo
        nome_dia_semana = dias_nomes[dia_semana_idx]
        data_formatada = dia_data.strftime("%d/%m")

        # Verifica se este dia da semana é um dia sugerido para treino
        is_training_day = dia_semana_idx in suggested_day_indices

        with cols[i]:
            # Define o estilo do "cartão" do dia
            background_color = "#2E4053" if is_training_day else "#1C2833"  # Cor mais escura para descanso
            border_style = "2px solid #5DADE2" if is_training_day else "1px solid #566573"  # Borda destacada para treino

            st.markdown(f"""
            <div style="background-color:{background_color}; border:{border_style}; border-radius:10px; padding:15px; text-align:center; height:150px; display:flex; flex-direction:column; justify-content:space-between;">
                <div style="font-weight:bold; font-size:1.1em;">{nome_dia_semana}</div>
                <div style="font-size:0.9em; color:#AEB6BF;">{data_formatada}</div>
            """, unsafe_allow_html=True)

            if is_training_day:
                # Pega o próximo nome de treino do ciclo
                nome_treino_do_dia = next(ciclo_treinos)
                st.markdown(f"""
                    <div style="font-size:1.5em;">💪</div>
                    <div style="font-weight:bold; color:#5DADE2;">Treino</div>
                    <div style="font-size:0.8em; color:#AEB6BF;">({nome_treino_do_dia.split(':')[0]})</div> 
                    </div> 
                """, unsafe_allow_html=True)  # Fecha o div do cartão
            else:
                st.markdown(f"""
                    <div style="font-size:1.5em;">🧘</div>
                    <div style="color:#85929E;">Descanso</div>
                    </div>
                """, unsafe_allow_html=True)  # Fecha o div do cartão

    st.markdown("---")
    st.caption("Este é um planejamento sugerido. Sinta-se à vontade para ajustar à sua rotina.")


def suggest_days(dias_sem: int):
    if dias_sem <= 0: return []
    step = 7 / dias_sem
    return sorted(list(set([int(round(i * step)) % 7 for i in range(dias_sem)])))


def render_metas():
    st.title("🎯 Metas")
    with st.form("form_meta"):
        descricao = st.text_input("Descrição")
        alvo = st.number_input("Valor Alvo", 0.0, format="%.1f")
        prazo = st.date_input("Prazo", min_value=datetime.now().date())
        if st.form_submit_button("Adicionar"):
            metas = st.session_state.get('metas', [])
            metas.append(
                {'descricao': descricao, 'valor_alvo': alvo, 'prazo': prazo.isoformat(), 'criada_em': iso_now(),
                 'concluida': False})
            st.session_state['metas'] = metas
            uid = st.session_state.get('user_uid')
            if uid: salvar_dados_usuario_firebase(uid)
            st.success("Meta adicionada.")
    for i, m in enumerate(st.session_state.get('metas', [])):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"{m['descricao']} - Alvo: {m['valor_alvo']} - Prazo: {m['prazo']}")
        with col2:
            if st.button("✅ Concluir", key=f"conq_{i}"):
                st.session_state['metas'][i]['concluida'] = True
                uid = st.session_state.get('user_uid')
                if uid: salvar_dados_usuario_firebase(uid)
                st.success("Meta concluída.");
                st.rerun()


def render_nutricao():
    st.title("🥗 Nutrição Básica")
    dados = st.session_state.get('dados_usuario') or {}
    sexo = st.selectbox("Sexo", ["Masculino", "Feminino"])
    peso = st.number_input("Peso (kg)", value=dados.get('peso', 70.0))
    altura = st.number_input("Altura (cm)", value=dados.get('altura', 170.0))
    idade = st.number_input("Idade", value=dados.get('idade', 25))
    objetivo = st.selectbox("Objetivo", ["Manutenção", "Emagrecimento", "Hipertrofia"])
    if st.button("Calcular TMB e macros"):
        tmb = calcular_tmb(sexo, peso, altura, idade)
        macros = sugerir_macros(tmb, objetivo, peso)
        st.metric("TMB estimada", f"{int(tmb)} kcal/dia")
        st.write("Sugestão de macros:", macros)


def calcular_tmb(sexo: str, peso: float, altura_cm: float, idade: int) -> float:
    if sexo.lower().startswith('m'): return 10 * peso + 6.25 * altura_cm - 5 * idade + 5
    return 10 * peso + 6.25 * altura_cm - 5 * idade - 161


def sugerir_macros(tmb: float, objetivo: str, peso_kg: float):
    calorias = tmb * 1.55
    if objetivo == 'Emagrecimento':
        calorias *= 0.8
    elif objetivo == 'Hipertrofia':
        calorias *= 1.15
    prote = 1.8 * peso_kg
    prote_kcal = prote * 4
    gord_kcal = calorias * 0.25
    gord = gord_kcal / 9
    carbs_kcal = calorias - (prote_kcal + gord_kcal)
    carbs = carbs_kcal / 4 if carbs_kcal > 0 else 0
    return {'calorias': round(calorias), 'proteina_g': round(prote, 1), 'gordura_g': round(gord, 1),
            'carbs_g': round(carbs, 1)}


def render_busca():
    st.title("🔎 Busca")
    q = st.text_input("Pesquisar exercícios / histórico / treinos")
    if q:
        exs = [name for name in EXERCICIOS_DB.keys() if q.lower() in name.lower()]
        st.subheader("Exercícios encontrados");
        st.write(exs)
        hist = st.session_state.get('historico_treinos', [])
        matches = [h for h in hist if q.lower() in h.get('exercicio', '').lower()]
        st.subheader("No histórico");
        st.dataframe(pd.DataFrame(matches))


def render_export_backup():
    st.title("📤 Export / Backup")

    # --- Secção de Backup (existente) ---
    payload = {k: st.session_state.get(k) for k in
               ['dados_usuario', 'frequencia', 'historico_treinos', 'metas', 'fotos_progresso', 'medidas']}
    payload['plano_treino'] = plan_to_serial(st.session_state.get('plano_treino'))
    js = json.dumps(payload, default=str, ensure_ascii=False)
    st.download_button("📥 Baixar backup JSON", data=js, file_name="fitpro_backup.json", mime="application/json")
    if st.session_state.get('historico_treinos'):
        df = pd.DataFrame(st.session_state['historico_treinos'])
        st.download_button("📥 Exportar histórico CSV", data=df.to_csv(index=False), file_name="historico_treinos.csv", mime="text/csv")

    # Botão para criar backup online
    if st.button("Criar backup na coleção 'backups'"):
        uid = st.session_state.get('user_uid')
        if uid and uid != 'demo-uid':
            try:
                db.collection('backups').add({'uid': uid, 'payload': payload, 'created': datetime.now()})
                st.success("Backup criado na coleção 'backups'.")
            except Exception as e:
                st.error(f"Erro ao criar backup online: {e}")
        elif uid == 'demo-uid':
             st.info("Backup online não disponível para modo demo.")
        else:
             st.error("Usuário não identificado para backup online.")

    # --- [CORREÇÃO DE INDENTAÇÃO AQUI] ---
    # Este bloco inteiro foi movido um nível para a esquerda
    st.markdown("---") # Separador visual

    st.subheader("⚠️ Resetar Progresso")
    st.warning("Atenção: Esta ação apagará permanentemente todo o seu histórico de frequência e treinos registrados. Use com cuidado.")

    if 'confirm_reset' not in st.session_state:
        st.session_state.confirm_reset = False

    if st.session_state.confirm_reset:
        st.error("Tem certeza que deseja apagar todo o progresso? Esta ação não pode ser desfeita.")
        col1, col2, _ = st.columns([1,1,3])
        with col1:
            if st.button("✅ Sim, apagar tudo", type="primary", use_container_width=True):
                uid = st.session_state.get('user_uid')
                if uid and uid != 'demo-uid':
                    with st.spinner("Apagando progresso..."):
                        st.session_state['frequencia'] = []
                        st.session_state['historico_treinos'] = []
                        st.session_state['ciclo_atual'] = None
                        salvar_dados_usuario_firebase(uid)
                    st.success("Progresso resetado com sucesso!")
                    st.session_state.confirm_reset = False
                    time.sleep(1)
                    st.rerun()
                elif uid == 'demo-uid':
                    st.info("Reset não aplicável ao modo demo.")
                    st.session_state.confirm_reset = False
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Usuário não identificado para reset.")
                    st.session_state.confirm_reset = False

        with col2:
            if st.button("❌ Cancelar", use_container_width=True):
                st.session_state.confirm_reset = False
                st.rerun()
    else:
        if st.button("Resetar Histórico de Treinos", type="secondary"):
            st.session_state.confirm_reset = True
            st.rerun()


def render_admin_panel():
    st.title("👑 Painel Admin")
    st.warning("Use com cuidado — ações afetam usuários reais.")
    try:
        users = list(db.collection('usuarios').stream())
    except Exception:
        st.error("Erro ao listar usuários."); return
    st.write(f"Total usuários: {len(users)}")
    for u in users:
        d = u.to_dict()
        nome = d.get('username', (d.get('dados_usuario') or {}).get('nome', '-'))
        st.write(f"- {nome} ({u.id}) - treinos: {len(d.get('frequencia', []))} - role: {d.get('role')}")
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            if st.button("Ver dados", key=f"ver_{u.id}"): st.json(d)
        with c2:
            if d.get('role') != 'admin' and st.button("Promover", key=f"prom_{u.id}"):
                db.collection('usuarios').document(u.id).update({'role': 'admin'});
                st.success("Promovido a admin.");
                st.rerun()
        with c3:
            if st.button("Excluir", key=f"del_{u.id}"):
                st.session_state['user_to_delete'] = u.id;
                st.session_state['confirm_delete_user'] = True;
                st.rerun()
    if st.session_state.get('confirm_delete_user'):
        st.warning("Confirmar exclusão do usuário (irrevogável).")
        ca, cb = st.columns(2)
        with ca:
            if st.button("✅ Confirmar exclusão"):
                uid = st.session_state.get('user_to_delete')
                if uid:
                    try:
                        try:
                            auth.delete_user(uid)
                        except Exception:
                            pass
                        db.collection('usuarios').document(uid).delete()
                        st.success("Usuário excluído.")
                    except Exception as e:
                        st.error(f"Erro ao excluir: {e}")
                st.session_state['confirm_delete_user'] = False;
                st.session_state['user_to_delete'] = None;
                st.rerun()
        with cb:
            if st.button("❌ Cancelar"):
                st.session_state['confirm_delete_user'] = False;
                st.session_state['user_to_delete'] = None;
                st.rerun()


# ---------------------------
# Run app
# ---------------------------
def run():
    if not st.session_state.get('usuario_logado'):
        uid_from_cookie = cookies.get('user_uid')
        if uid_from_cookie:
            try:
                doc = db.collection('usuarios').document(uid_from_cookie).get()
                if doc.exists:
                    st.session_state['user_uid'] = uid_from_cookie
                    st.session_state['usuario_logado'] = doc.to_dict().get('username', 'Usuário')
                    carregar_dados_usuario_firebase(uid_from_cookie)
                else:
                    del cookies['user_uid']
            except Exception as e:
                st.error(f"Erro ao tentar login automático: {e}")
    if not st.session_state.get('usuario_logado'):
        render_auth()
    else:
        render_main()


if __name__ == "__main__":
    run()