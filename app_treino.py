# app_treino.py (Versão Completa com Gamificação, Treino Ao Vivo e Rede Social)
"""
FitPro - App completo pronto para deploy
- Spinner em operações de I/O (salvar/carregar)
- Confirmações elegantes (st.dialog() quando disponível, fallback)
- Cards visuais para treinos + gráfico por exercício
- Calendário visual de treinos
- Firebase (Auth + Firestore) via st.secrets["firebase_credentials"]
- Compatibilidade Streamlit (st.rerun fallback)
- Geração de treino totalmente personalizada baseada em questionário.
- Lógica de substituição de exercícios baseada em restrições.
- Banco de exercícios expandido com categorias e alternativas.
- Login persistente com cookies para não deslogar ao atualizar a página.
- Uso de st.cache_resource para otimizar a conexão com Firebase.
- Guia visual com GIFs para cada exercício na tela "Meu Treino".
- Sistema de Gamificação:
  - XP e Níveis de usuário.
  - Conquistas (Badges) baseadas em marcos.
  - Streak (dias consecutivos) de treinos.
  - Página dedicada para perfil e conquistas.
- Sistema de Treino com Timer Integrado:
  - Cronômetro de descanso entre séries.
  - Registro rápido durante o treino.
  - Modo "treino em andamento" com checklist.
- Rede Social Interna:
  - Feed de progresso de amigos (seguidores).
  - Comentários e reações (curtidas).
  - Sistema de seguir/deixar de seguir usuários.
"""
import os
import re
import io
import json
import time
import base64
import logging
import requests
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from PIL import Image, ImageChops, ImageFilter, ImageStat
from streamlit_cookies_manager import CookieManager

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
# Session defaults com novos recursos
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
        # Gamificação
        'xp': 0, 'nivel': 1, 'streak_atual': 0, 'streak_maximo': 0, 'ultimo_treino_data': None, 'conquistas': [],
        # Treino Ao Vivo
        'treino_ativo': False, 'plano_treino_ativo': None, 'logs_treino_ativo': {}, 'timer_ativo': False,
        'timer_fim': None,
        # Social
        'seguindo': [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


ensure_session_defaults()


# ---------------------------
# Funções de Rede Social
# ---------------------------
def criar_post_feed(uid: str, nome_usuario: str, tipo: str, texto: str):
    """Cria um post no feed global."""
    if not uid or uid == 'demo-uid': return
    try:
        db.collection('feed').add({
            'uid_autor': uid,
            'nome_autor': nome_usuario,
            'tipo': tipo,  # 'treino_concluido', 'conquista', 'meta'
            'texto': texto,
            'timestamp': datetime.now(),
            'curtidas': [],
        })
    except Exception as e:
        print(f"Erro ao criar post no feed: {e}")


# ---------------------------
# Configurações de Gamificação
# ---------------------------
XP_POR_TREINO = 50
XP_POR_META_CONCLUIDA = 100
CONQUISTAS_DB = {
    'primeiro_treino': {'nome': 'Início da Jornada', 'descricao': 'Complete seu primeiro treino.', 'icone': '🚀'},
    'semana_perfeita': {'nome': 'Semana Perfeita', 'descricao': 'Treine 7 dias seguidos.', 'icone': '🔥'},
    'mes_consistente': {'nome': 'Mês Consistente', 'descricao': 'Complete 20 treinos em um mês.', 'icone': '🗓️'},
    'fotografo': {'nome': 'Fotógrafo', 'descricao': 'Adicione sua primeira foto de progresso.', 'icone': '📸'},
    'supino_100kg': {'nome': 'Clube dos 100kg', 'descricao': 'Levante 100kg no Supino Reto.', 'icone': '💪'},
    'maratonista': {'nome': 'Maratonista', 'descricao': 'Registre 50 treinos no total.', 'icone': '🏃‍♂️'},
}


# ---------------------------
# Funções de Lógica de Gamificação
# ---------------------------
def calcular_nivel(xp: int) -> int:
    return int((xp / 100) ** 0.5) + 1


def adicionar_xp(valor: int):
    if 'xp' not in st.session_state: st.session_state['xp'] = 0
    if 'nivel' not in st.session_state: st.session_state['nivel'] = 1
    st.session_state['xp'] += valor
    novo_nivel = calcular_nivel(st.session_state['xp'])
    if novo_nivel > st.session_state['nivel']:
        st.session_state['nivel'] = novo_nivel
        st.balloons()
        st.success(f"🎉 Você subiu para o Nível {novo_nivel}!")
    st.info(f"+{valor} XP!")


def verificar_e_conceder_conquistas():
    conquistas_ganhas = st.session_state.get('conquistas', [])
    uid = st.session_state.get('user_uid')
    nome_usuario = st.session_state.get('usuario_logado', 'Usuário')

    def conceder(chave_conquista: str):
        if chave_conquista not in conquistas_ganhas:
            info = CONQUISTAS_DB[chave_conquista]
            st.session_state['conquistas'].append(chave_conquista)
            st.success(f"🏆 Conquista Desbloqueada: {info['nome']}!")
            if uid and uid != 'demo-uid':
                db.collection('usuarios').document(uid).collection('conquistas').document(chave_conquista).set(
                    {'data_conquista': datetime.now(), 'nome': info['nome']})
                criar_post_feed(uid, nome_usuario, 'conquista',
                                f"Desbloqueou a conquista: {info['nome']} {info['icone']}")

    # Regras de conquista...
    if len(st.session_state.get('historico_treinos', [])) >= 1: conceder('primeiro_treino')
    if st.session_state.get('streak_atual', 0) >= 7: conceder('semana_perfeita')
    hoje = date.today()
    treinos_registrados = st.session_state.get('frequencia', [])
    treinos_30_dias = [t for t in treinos_registrados if isinstance(t, date) and (hoje - t).days <= 30]
    if len(set(treinos_30_dias)) >= 20: conceder('mes_consistente')
    if len(st.session_state.get('fotos_progresso', [])) >= 1: conceder('fotografo')
    if len(set(st.session_state.get('frequencia', []))) >= 50: conceder('maratonista')
    hist_supino = [t for t in st.session_state.get('historico_treinos', []) if
                   t.get('exercicio') == 'Supino Reto com Barra' and t.get('peso', 0) >= 100]
    if hist_supino: conceder('supino_100kg')


def atualizar_streak(data_treino: date):
    ultimo_treino = st.session_state.get('ultimo_treino_data')
    if ultimo_treino is None:
        st.session_state['streak_atual'] = 1
    elif isinstance(ultimo_treino, date):
        diferenca = (data_treino - ultimo_treino).days
        if diferenca == 1:
            st.session_state['streak_atual'] += 1
        elif diferenca > 1:
            st.session_state['streak_atual'] = 1
    st.session_state['ultimo_treino_data'] = data_treino
    if st.session_state['streak_atual'] > st.session_state.get('streak_maximo', 0):
        st.session_state['streak_maximo'] = st.session_state['streak_atual']
    if st.session_state['streak_atual'] > 1:
        st.info(f"🔥 Você está há {st.session_state['streak_atual']} dias seguidos treinando!")


# ---------------------------
# Banco de Exercícios e Lógica de Plano (sem alterações)
# ---------------------------
EXERCICIOS_DB = {
    # Pernas
    'Agachamento com Barra': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra',
                              'restricoes': ['Lombar', 'Joelhos']},
    'Agachamento Goblet': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Joelhos']},
    'Leg Press 45°': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': []},
    'Cadeira Extensora': {'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': []},
    'Mesa Flexora': {'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': []},
    'Stiff com Halteres': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Lombar']},
    'Elevação Pélvica': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Barra', 'restricoes': []},
    'Panturrilha no Leg Press': {'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': []},

    # Peito
    'Supino Reto com Barra': {'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Ombros']},
    'Supino Reto com Halteres': {'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': []},
    'Supino Inclinado com Halteres': {'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres',
                                      'restricoes': []},
    'Crucifixo com Halteres': {'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': []},
    'Flexão de Braço': {'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Punhos']},

    # Costas
    'Barra Fixa': {'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': []},
    'Puxada Alta (Lat Pulldown)': {'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': []},
    'Remada Curvada com Barra': {'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra',
                                 'restricoes': ['Lombar']},
    'Remada Sentada (máquina)': {'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': []},
    'Remada Unilateral (Serrote)': {'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': []},

    # Ombros
    'Desenvolvimento Militar com Barra': {'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Barra',
                                          'restricoes': ['Lombar', 'Ombros']},
    'Desenvolvimento com Halteres (sentado)': {'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Halteres',
                                               'restricoes': []},
    'Elevação Lateral': {'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': []},
    'Elevação Frontal': {'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': []},

    # Bíceps
    'Rosca Direta com Barra': {'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': ['Punhos']},
    'Rosca Direta com Halteres': {'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': []},
    'Rosca Martelo': {'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': []},

    # Tríceps
    'Tríceps Testa': {'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres',
                      'restricoes': ['Cotovelos']},
    'Tríceps Pulley': {'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': []},
    'Mergulho no Banco': {'grupo': 'Tríceps', 'tipo': 'Composto', 'equipamento': 'Peso Corporal',
                          'restricoes': ['Ombros', 'Punhos']},

    # Core
    'Prancha': {'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': []},
    'Abdominal Crunch': {'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': []},
    'Elevação de Pernas': {'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal',
                           'restricoes': ['Lombar']},
}
EXERCISE_SUBSTITUTIONS = {
    'Agachamento com Barra': 'Leg Press 45°', 'Stiff com Halteres': 'Mesa Flexora',
    'Remada Curvada com Barra': 'Remada Sentada (máquina)',
    'Desenvolvimento Militar com Barra': 'Desenvolvimento com Halteres (sentado)',
    'Supino Reto com Barra': 'Supino Reto com Halteres', 'Tríceps Testa': 'Tríceps Pulley',
    'Rosca Direta com Barra': 'Rosca Direta com Halteres', 'Flexão de Braço': 'Supino Reto com Halteres',
    'Elevação de Pernas': 'Prancha'
}


@st.cache_data(ttl=3600 * 24)
def find_exercise_gif(exercise_name: str) -> Optional[str]:
    try:
        search_term = f"{exercise_name} exercise animated gif"
        params = {"q": search_term, "key": "LIVDSRZULELA", "limit": 1, "media_filter": "minimal"}
        response = requests.get("https://g.tenor.com/v1/search", params=params)
        response.raise_for_status()
        results = response.json()
        if results['results']: return results['results'][0]['media'][0]['gif']['url']
    except Exception as e:
        print(f"Não foi possível buscar o GIF para '{exercise_name}': {e}")
    return None


def plan_to_serial(plano: Optional[Dict[str, Any]]):
    if not plano: return None
    out = {}
    for k, v in plano.items():
        if isinstance(v, pd.DataFrame):
            out[k] = v.to_dict(orient='records')
        else:
            out[k] = v
    return out


def serial_to_plan(serial: Optional[Dict[str, Any]]):
    if not serial: return None
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
# Firestore save/load (com adição de 'seguindo')
# ---------------------------
def salvar_dados_usuario_firebase(uid: str):
    if not uid or uid == 'demo-uid': return
    try:
        with st.spinner("💾 Salvando dados no Firestore..."):
            doc = db.collection('usuarios').document(uid)
            plano_serial = plan_to_serial(st.session_state.get('plano_treino'))
            freq = [
                datetime.combine(d, datetime.min.time()) if isinstance(d, date) and not isinstance(d, datetime) else d
                for d in st.session_state.get('frequencia', [])]
            hist = []
            for t in st.session_state.get('historico_treinos', []):
                copy = dict(t)
                if 'data' in copy and isinstance(copy['data'], date) and not isinstance(copy['data'], datetime):
                    copy['data'] = datetime.combine(copy['data'], datetime.min.time())
                hist.append(copy)
            metas_save = []
            for m in st.session_state.get('metas', []):
                copy = dict(m)
                if 'prazo' in copy and isinstance(copy['prazo'], date): copy['prazo'] = datetime.combine(copy['prazo'],
                                                                                                         datetime.min.time())
                metas_save.append(copy)
            ultimo_treino_salvar = st.session_state.get('ultimo_treino_data')
            if isinstance(ultimo_treino_salvar, date) and not isinstance(ultimo_treino_salvar, datetime):
                ultimo_treino_salvar = datetime.combine(ultimo_treino_salvar, datetime.min.time())

            payload = {
                'dados_usuario': st.session_state.get('dados_usuario'),
                'plano_treino': plano_serial,
                'frequencia': freq, 'historico_treinos': hist,
                'historico_peso': st.session_state.get('historico_peso', []),
                'metas': metas_save, 'fotos_progresso': st.session_state.get('fotos_progresso', []),
                'medidas': st.session_state.get('medidas', []), 'feedbacks': st.session_state.get('feedbacks', []),
                'ciclo_atual': st.session_state.get('ciclo_atual'), 'role': st.session_state.get('role'),
                'settings': st.session_state.get('settings', {}),
                'xp': st.session_state.get('xp', 0), 'nivel': st.session_state.get('nivel', 1),
                'streak_atual': st.session_state.get('streak_atual', 0),
                'streak_maximo': st.session_state.get('streak_maximo', 0),
                'ultimo_treino_data': ultimo_treino_salvar, 'conquistas': st.session_state.get('conquistas', []),
                'seguindo': st.session_state.get('seguindo', []),  # Salva a lista de quem o usuário segue
                'ultimo_save': datetime.now()
            }
            doc.set(payload, merge=True)
            time.sleep(0.4)
        st.success("✅ Dados salvos!")
    except Exception as e:
        st.error(f"Erro ao salvar no Firestore: {e}")


def carregar_dados_usuario_firebase(uid: str):
    if not uid: return
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
        st.session_state['frequencia'] = [d.date() if isinstance(d, datetime) else d for d in
                                          data.get('frequencia', [])]
        hist = data.get('historico_treinos', [])
        for t in hist:
            if 'data' in t and isinstance(t['data'], datetime): t['data'] = t['data'].date()
        st.session_state['historico_treinos'] = hist
        st.session_state['fotos_progresso'] = data.get('fotos_progresso', [])
        st.session_state['medidas'] = data.get('medidas', [])
        st.session_state['feedbacks'] = data.get('feedbacks', [])
        st.session_state['metas'] = data.get('metas', [])
        st.session_state['role'] = data.get('role')
        st.session_state['settings'] = data.get('settings', st.session_state.get('settings', {}))
        st.session_state['xp'] = data.get('xp', 0)
        st.session_state['nivel'] = data.get('nivel', 1)
        st.session_state['streak_atual'] = data.get('streak_atual', 0)
        st.session_state['streak_maximo'] = data.get('streak_maximo', 0)
        st.session_state['conquistas'] = data.get('conquistas', [])
        st.session_state['seguindo'] = data.get('seguindo', [])
        ultimo_treino_db = data.get('ultimo_treino_data')
        if isinstance(ultimo_treino_db, datetime):
            st.session_state['ultimo_treino_data'] = ultimo_treino_db.date()

    except Exception as e:
        st.error(f"Erro ao carregar do Firestore: {e}")


# ---------------------------
# Auth helpers (com adição de 'seguindo' no cadastro)
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
            'plano_treino': None, 'frequencia': [], 'historico_treinos': [], 'historico_peso': [],
            'metas': [], 'fotos_progresso': [], 'medidas': [], 'feedbacks': [],
            'ciclo_atual': None, 'role': None, 'password_hash': sha256(senha),
            'data_criacao': datetime.now(),
            'xp': 0, 'nivel': 1, 'streak_atual': 0, 'streak_maximo': 0, 'ultimo_treino_data': None,
            'conquistas': [], 'seguindo': [],
        })
        return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"


def verificar_credenciais_firebase(username_or_email: str, senha: str) -> (bool, str):
    if username_or_email == 'demo' and senha == 'demo123':
        st.session_state['user_uid'] = 'demo-uid'
        st.session_state['usuario_logado'] = 'Demo'
        ensure_session_defaults()
        st.session_state['dados_usuario'] = {'nome': 'Demo', 'peso': 75, 'altura': 175,
                                             'nivel': 'Intermediário/Avançado', 'dias_semana': 4,
                                             'objetivo': 'Hipertrofia', 'restricoes': ['Lombar']}
        st.session_state['plano_treino'] = gerar_plano_personalizado(st.session_state['dados_usuario'])
        return True, "Modo demo ativado."
    try:
        user = auth.get_user_by_email(username_or_email)
        uid = user.uid
        doc = db.collection('usuarios').document(uid).get()
        if not doc.exists: return False, "Usuário sem documento no Firestore."
        data = doc.to_dict()
        stored_hash = data.get('password_hash')
        if stored_hash and stored_hash == sha256(senha):
            st.session_state['user_uid'] = uid
            st.session_state['usuario_logado'] = data.get('username') or username_or_email
            carregar_dados_usuario_firebase(uid)
            cookies['user_uid'] = uid
            cookies.save()
            return True, f"Bem-vindo(a), {st.session_state['usuario_logado']}!"
        else:
            return False, "Senha incorreta."
    except auth.UserNotFoundError:
        return False, "Usuário não encontrado."
    except Exception as e:
        return False, f"Erro ao autenticar: {e}"


# ---------------------------
# Periodization & Notifications (sem alterações)
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
        if num_treinos == t: notifs.append({'tipo': 'conquista', 'msg': f"🎉 Você alcançou {t} treinos!"})
    st.session_state['notificacoes'] = notifs


# ---------------------------
# UI & Plan Generation (sem alterações)
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
                if st.button("❌ Cancelar"): st.rerun()
            with c2:
                if st.button("✅ Confirmar"):
                    fotos = st.session_state.get('fotos_progresso', [])
                    if 0 <= idx < len(fotos):
                        fotos.pop(idx)
                        st.session_state['fotos_progresso'] = fotos
                        if uid: salvar_dados_usuario_firebase(uid)
                        st.success("Foto excluída.")
                        st.rerun()

        inner()
    else:
        st.session_state['foto_a_excluir'] = idx
        st.session_state['confirm_excluir_foto'] = True


def gerar_plano_personalizado(dados_usuario: Dict[str, Any], fase_atual: Optional[Dict] = None) -> Dict:
    nivel = dados_usuario.get('nivel', 'Iniciante')
    dias = dados_usuario.get('dias_semana', 3)
    objetivo = dados_usuario.get('objetivo', 'Hipertrofia')
    restricoes_usr = dados_usuario.get('restricoes', [])

    if fase_atual:
        series_base, reps_base, descanso_base = fase_atual['series'], fase_atual['reps'], fase_atual['descanso']
    else:
        if objetivo == 'Hipertrofia':
            series_base, reps_base, descanso_base = '3-4', '8-12', '60-90s'
        elif objetivo == 'Emagrecimento':
            series_base, reps_base, descanso_base = '3', '12-15', '45-60s'
        else:
            series_base, reps_base, descanso_base = '3', '15-20', '30-45s'

    def selecionar_exercicios(grupos: List[str], n_compostos: int, n_isolados: int) -> List[Dict]:
        exercicios_selecionados = []
        candidatos_validos = []
        for ex_nome, ex_data in EXERCICIOS_DB.items():
            if ex_data['grupo'] in grupos:
                if any(r in ex_data.get('restricoes', []) for r in restricoes_usr):
                    substituto = EXERCISE_SUBSTITUTIONS.get(ex_nome)
                    if substituto and substituto not in candidatos_validos: candidatos_validos.append(substituto)
                else:
                    candidatos_validos.append(ex_nome)
        candidatos = list(set(candidatos_validos))
        compostos = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] == 'Composto'][:n_compostos]
        isolados = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] != 'Composto' and ex not in compostos][
                   :n_isolados]
        for ex in compostos + isolados:
            exercicios_selecionados.append(
                {'Exercício': ex, 'Séries': series_base.split('-')[-1], 'Repetições': reps_base,
                 'Descanso': descanso_base})
        return exercicios_selecionados

    plano = {}
    if nivel == 'Iniciante' or dias <= 2:
        plano['Treino A: Corpo Inteiro'] = selecionar_exercicios(['Peito', 'Costas', 'Pernas', 'Ombros'], 3, 1)
        plano['Treino B: Corpo Inteiro'] = selecionar_exercicios(['Pernas', 'Costas', 'Peito', 'Bíceps', 'Tríceps'], 3,
                                                                 2)
    elif dias == 3:
        plano['Treino A: Superiores (Push)'] = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 2, 3)
        plano['Treino B: Inferiores'] = selecionar_exercicios(['Pernas'], 2, 3)
        plano['Treino C: Superiores (Pull)'] = selecionar_exercicios(['Costas', 'Bíceps'], 2, 2)
    elif dias == 4:
        plano['Treino A: Superiores (Foco Peito/Costas)'] = selecionar_exercicios(['Peito', 'Costas', 'Bíceps'], 3, 2)
        plano['Treino B: Inferiores (Foco Quadríceps)'] = selecionar_exercicios(['Pernas'], 2, 3)
        plano['Treino C: Superiores (Foco Ombros/Braços)'] = selecionar_exercicios(['Ombros', 'Tríceps', 'Bíceps'], 2,
                                                                                   3)
        plano['Treino D: Inferiores (Foco Posterior/Glúteos)'] = selecionar_exercicios(['Pernas'], 2, 3)
    elif dias >= 5:
        plano['Treino A: Peito'] = selecionar_exercicios(['Peito'], 2, 2)
        plano['Treino B: Costas'] = selecionar_exercicios(['Costas'], 2, 2)
        plano['Treino C: Pernas'] = selecionar_exercicios(['Pernas'], 2, 3)
        plano['Treino D: Ombros'] = selecionar_exercicios(['Ombros'], 2, 2)
        plano['Treino E: Braços & Core'] = selecionar_exercicios(['Bíceps', 'Tríceps', 'Core'], 0, 4)

    for nome, exercicios in plano.items():
        if exercicios:
            plano[nome] = pd.DataFrame(exercicios)
        else:
            plano[nome] = pd.DataFrame()
    return plano


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
            submit_login = st.form_submit_button("Entrar", use_container_width=True)
            if st.form_submit_button("👁️ Usar Modo Demo", use_container_width=True):
                ok, msg = verificar_credenciais_firebase('demo', 'demo123')
                if ok:
                    st.success(msg); st.rerun()
                else:
                    st.error(msg)
            if submit_login:
                if not username or not senha:
                    st.error("Preencha e-mail e senha.")
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
            senha = st.text_input("Senha (mínimo 6 caracteres)", type='password')
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
    if st.session_state.get('treino_ativo'):
        render_treino_ao_vivo()
        st.stop()

    check_notifications_on_open()
    st.sidebar.title("🏋️ FitPro")
    st.sidebar.write(f"👤 {st.session_state.get('usuario_logado')}")
    if st.sidebar.button("🚪 Sair"):
        uid = st.session_state.get('user_uid')
        if uid: salvar_dados_usuario_firebase(uid)
        cookies['user_uid'] = ''
        cookies.save()
        keys_to_keep = {'db'}
        for k in list(st.session_state.keys()):
            if k not in keys_to_keep: del st.session_state[k]
        ensure_session_defaults()
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
        if st.sidebar.button("Painel Admin"): st.session_state['page'] = 'Admin'
    if st.session_state.get('notificacoes'):
        for n in st.session_state['notificacoes']:
            if n['tipo'] == 'conquista':
                st.balloons(); st.success(n['msg'])
            else:
                try:
                    st.toast(n['msg'])
                except Exception:
                    st.info(n['msg'])

    pages = ["Dashboard", "Meu Perfil", "Rede Social", "Questionário", "Meu Treino", "Registrar Treino", "Progresso",
             "Fotos", "Comparar Fotos", "Medidas", "Planejamento Semanal", "Metas", "Nutrição", "Busca",
             "Export/Backup"]
    if st.session_state.get('role') == 'admin': pages.append("Admin")
    page = st.selectbox("Navegação", pages)

    page_map = {
        "Dashboard": render_dashboard, "Meu Perfil": render_gamificacao, "Rede Social": render_rede_social,
        "Questionário": render_questionario,
        "Meu Treino": render_meu_treino, "Registrar Treino": render_registrar_treino, "Progresso": render_progresso,
        "Fotos": render_fotos, "Comparar Fotos": render_comparar_fotos, "Medidas": render_medidas,
        "Planejamento Semanal": render_planner, "Metas": render_metas, "Nutrição": render_nutricao,
        "Busca": render_busca, "Export/Backup": render_export_backup, "Admin": render_admin_panel,
    }
    render_func = page_map.get(page, lambda: st.write("Página em desenvolvimento."))
    render_func()


# ---------------------------
# Page implementations (demais páginas sem alterações)
# ---------------------------
def render_dashboard():
    st.title("📊 Dashboard")
    num_treinos = len(set(st.session_state.get('frequencia', [])))
    col1, col2, col3 = st.columns(3)
    col1.metric("Nível", st.session_state.get('nivel', 1))
    col2.metric("Treinos Completos", num_treinos)
    col3.metric("Streak Atual 🔥", f"{st.session_state.get('streak_atual', 0)} dias")
    if num_treinos > 0:
        info = verificar_periodizacao(num_treinos)
        fase = info['fase_atual']
        st.markdown(
            f"""<div style='padding:20px;border-radius:12px;background:linear-gradient(90deg,{fase['cor']},#ffffff);color:#111; margin-top: 20px;'><h3>🎯 Fase Atual: {fase['nome']} | Ciclo {info['numero_ciclo']}</h3><p>{fase['reps']} reps · {fase['series']} séries · Descanso {fase['descanso']}</p></div>""",
            unsafe_allow_html=True)
    if st.session_state.get('medidas'):
        dfm = pd.DataFrame(st.session_state['medidas'])
        dfm['data'] = pd.to_datetime(dfm['data'])
        fig = px.line(dfm, x='data', y='valor', color='tipo', markers=True, title='Evolução de Medidas')
        st.plotly_chart(fig, use_container_width=True)
    st.subheader("📅 Calendário de Treinos (últimos 30 dias)")
    if st.session_state.get('frequencia'):
        hoje = date.today()
        ult30 = [hoje - timedelta(days=i) for i in range(29, -1, -1)]
        treinos_30 = set(st.session_state['frequencia'])
        eventos = [{'date': d.isoformat(), 'display': 'background', 'color': 'green'} for d in ult30 if d in treinos_30]
        try:
            from streamlit_calendar import calendar
            calendar(events=eventos,
                     options={"headerToolbar": {"left": "today prev,next", "center": "title", "right": ""},
                              "initialDate": hoje.isoformat(), "height": "400px"})
        except ImportError:
            st.warning("Para ver o calendário visual, instale `streamlit-calendar`: pip install streamlit-calendar")
            st.write([d.isoformat() for d in treinos_30])
    else:
        st.info("Registre treinos para ver o calendário.")


def render_gamificacao():
    st.title("🏆 Meu Perfil e Conquistas")
    xp = st.session_state.get('xp', 0)
    nivel = st.session_state.get('nivel', 1)
    xp_proximo_nivel = (nivel ** 2) * 100
    col1, col2, col3 = st.columns(3)
    col1.metric("Nível", nivel)
    col2.metric("Streak Atual", f"{st.session_state.get('streak_atual', 0)} dias 🔥")
    col3.metric("Recorde de Streak", f"{st.session_state.get('streak_maximo', 0)} dias")
    st.progress(xp / xp_proximo_nivel if xp_proximo_nivel > 0 else 0,
                text=f"{xp} / {xp_proximo_nivel} XP para o próximo nível")
    st.markdown("---")
    st.subheader("Minhas Conquistas")
    conquistas_ganhas = st.session_state.get('conquistas', [])
    if not conquistas_ganhas:
        st.info("Continue treinando para desbloquear novas conquistas!")
    else:
        cols = st.columns(4)
        col_idx = 0
        for chave in conquistas_ganhas:
            if chave in CONQUISTAS_DB:
                conquista = CONQUISTAS_DB[chave]
                with cols[col_idx % 4]:
                    st.markdown(
                        f"""<div style="text-align: center; padding: 15px; border: 1px solid #444; border-radius: 10px; height: 180px; margin-bottom: 10px;"><span style="font-size: 3em;">{conquista['icone']}</span><h5 style="margin-bottom: 5px; margin-top: 10px;">{conquista['nome']}</h5><small>{conquista['descricao']}</small></div>""",
                        unsafe_allow_html=True)
                    col_idx += 1
    st.markdown("---")
    st.subheader("Todas as Conquistas")
    for chave, conquista in CONQUISTAS_DB.items():
        if chave in conquistas_ganhas:
            st.success(f"{conquista['icone']} **{conquista['nome']}**: {conquista['descricao']}")
        else:
            st.warning(f"🔒 **{conquista['nome']}**: {conquista['descricao']}")


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
            novos_dados = {'nome': nome, 'idade': idade, 'peso': peso, 'altura': altura, 'nivel': nivel,
                           'objetivo': objetivo, 'dias_semana': dias, 'restricoes': restricoes,
                           'data_cadastro': iso_now()}
            st.session_state['dados_usuario'] = novos_dados
            hp = st.session_state.get('historico_peso', [])
            if not hp or hp[-1].get('peso') != peso: hp.append({'data': iso_now(), 'peso': peso}); st.session_state[
                'historico_peso'] = hp
            with st.spinner("🤖 Criando seu plano de treino personalizado..."):
                st.session_state['plano_treino'] = gerar_plano_personalizado(novos_dados)
                time.sleep(1)
            uid = st.session_state.get('user_uid')
            if uid: salvar_dados_usuario_firebase(uid)
            st.success("Perfil salvo e plano de treino personalizado gerado com sucesso!")
            st.info("Acesse a página 'Meu Treino' para visualizar.")


def render_meu_treino():
    st.title("💪 Meu Treino")
    plano = st.session_state.get('plano_treino')
    if not plano or all(df.empty for df in plano.values()):
        st.info("Você ainda não tem um plano de treino. Vá para a página 'Questionário' para gerar o seu primeiro!")
        return
    dados = st.session_state.get('dados_usuario') or {}
    st.info(
        f"Este plano foi criado para um atleta **{dados.get('nivel', '')}** treinando **{dados.get('dias_semana', '')}** dias por semana com foco em **{dados.get('objetivo', '')}**.")

    for nome_treino, df_treino in plano.items():
        if df_treino.empty: continue
        with st.container(border=True):
            st.subheader(nome_treino)
            if st.button(f"▶️ Iniciar Treino: {nome_treino}", key=f"start_{nome_treino}"):
                st.session_state['treino_ativo'] = True
                st.session_state['plano_treino_ativo'] = {'nome': nome_treino,
                                                          'df': df_treino.to_dict(orient='records')}
                st.session_state['logs_treino_ativo'] = {}
                st.rerun()

            for _, row in df_treino.iterrows():
                exercicio, series, repeticoes, descanso = row['Exercício'], row['Séries'], row['Repetições'], row[
                    'Descanso']
                with st.expander(f"**{exercicio}** | {series} Séries x {repeticoes} Reps"):
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        gif_url = find_exercise_gif(exercicio)
                        if gif_url:
                            st.image(gif_url, caption=f"Execução de {exercicio}")
                        else:
                            st.info("Guia visual indisponível.")
                    with col2:
                        st.markdown(f"##### 📋 **Instruções**")
                        st.markdown(
                            f"- **Séries:** `{series}`\n- **Repetições:** `{repeticoes}`\n- **Descanso:** `{descanso}` entre as séries")
                        st.markdown("---")
                        st.write(f"**Grupo Muscular:** {EXERCICIOS_DB.get(exercicio, {}).get('grupo', 'N/A')}")
                        st.write(f"**Equipamento:** {EXERCICIOS_DB.get(exercicio, {}).get('equipamento', 'N/A')}")
            hist = pd.DataFrame(st.session_state.get('historico_treinos', []))
            if not hist.empty:
                exs = df_treino['Exercício'].tolist()
                df_plot = hist[hist['exercicio'].isin(exs)].copy()
                if not df_plot.empty:
                    df_plot['data'] = pd.to_datetime(df_plot.get('data'))
                    fig = px.line(df_plot, x='data', y='peso', color='exercicio', markers=True,
                                  title=f'Evolução de cargas - {nome_treino}')
                    st.plotly_chart(fig, use_container_width=True)


def render_registrar_treino():
    st.title("📝 Registrar Treino")
    st.info("Para uma experiência interativa, inicie um treino a partir da página 'Meu Treino'.")
    with st.form("f_registrar"):
        data_treino = st.date_input("Data", datetime.now().date())
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
                novo = {'data': data_treino, 'tipo': tipo, 'exercicio': exercicio, 'series': int(series),
                        'reps': int(reps), 'peso': float(peso), 'volume': int(series) * int(reps) * float(peso),
                        'observacoes': obs, 'timestamp': iso_now()}
                st.session_state.historico_treinos.append(novo)
                frequencia = set(st.session_state.frequencia);
                frequencia.add(data_treino);
                st.session_state.frequencia = sorted(list(frequencia))
                adicionar_xp(XP_POR_TREINO)
                atualizar_streak(data_treino)
                verificar_e_conceder_conquistas()
                uid = st.session_state.get('user_uid')
                if uid: salvar_dados_usuario_firebase(uid)
                st.success("✅ Treino registrado.")


def render_progresso():
    st.title("📈 Progresso")
    hist = st.session_state.get('historico_treinos', [])
    if not hist: st.info("Registre treinos para ver gráficos."); return
    df = pd.DataFrame(hist);
    df['data'] = pd.to_datetime(df['data'])
    vol = df.groupby(df['data'].dt.date)['volume'].sum().reset_index()
    fig = px.line(vol, x='data', y='volume', title='Volume por dia', markers=True)
    st.plotly_chart(fig, use_container_width=True)
    vol['rolling'] = vol['volume'].rolling(7, min_periods=1).mean()
    if len(vol['rolling']) >= 8:
        last, prev = vol['rolling'].iloc[-1], vol['rolling'].iloc[-8]
        if prev > 0 and abs(last - prev) / prev < 0.05: st.warning(
            "Possível platô detectado (variação <5% nas últimas semanas).")


def render_fotos():
    st.title("📸 Fotos de Progresso")
    with st.expander("Adicionar nova foto"):
        uploaded = st.file_uploader("Selecione uma imagem (png/jpg)", type=['png', 'jpg', 'jpeg'])
        if uploaded:
            img = Image.open(uploaded).convert('RGB')
            st.image(img, caption='Preview', width=300)
            data_foto = st.date_input("Data da foto", datetime.now().date())
            peso_foto = st.number_input("Peso (kg)", min_value=20.0,
                                        value=st.session_state.get('dados_usuario', {}).get('peso', 70.0), step=0.1)
            nota = st.text_area("Notas (opcional)")
            if st.button("💾 Salvar foto"):
                b64 = b64_from_pil(img)
                st.session_state.fotos_progresso.append(
                    {'data': data_foto.isoformat(), 'peso': float(peso_foto), 'imagem': b64, 'nota': nota,
                     'timestamp': iso_now()})
                verificar_e_conceder_conquistas()
                uid = st.session_state.get('user_uid');
                if uid: salvar_dados_usuario_firebase(uid)
                st.success("Foto salva.");
                st.rerun()
    st.subheader("Galeria")
    fotos = sorted(st.session_state.get('fotos_progresso', []), key=lambda x: x.get('data', ''), reverse=True)
    if not fotos: st.info("Nenhuma foto ainda."); return
    for i, f in enumerate(fotos):
        c1, c2, c3 = st.columns([1, 3, 1])
        with c1:
            st.image(pil_from_b64(f['imagem']), width=140)
        with c2:
            st.write(f"📅 {f.get('data')}  ⚖️ {f.get('peso')}kg")
            if f.get('nota'): st.write(f"📝 {f.get('nota')}")
        with c3:
            if st.button("🗑️ Excluir", key=f"del_{i}", use_container_width=True): confirm_delete_photo_dialog(i,
                                                                                                              st.session_state.get(
                                                                                                                  'user_uid'))


def render_comparar_fotos():
    st.title("🔍 Comparar Fotos")
    fotos = st.session_state.get('fotos_progresso', [])
    if len(fotos) < 2: st.info("Adicione pelo menos duas fotos para comparar."); return
    options = {f"{i} - {f['data']} - {f.get('peso')}kg": i for i, f in enumerate(fotos)}
    col1, col2 = st.columns(2)
    sel1_key = col1.selectbox("Foto 1 (Antes)", options.keys(), index=len(options) - 1)
    sel2_key = col2.selectbox("Foto 2 (Depois)", options.keys(), index=0)
    idx1, idx2 = options[sel1_key], options[sel2_key]
    img1, img2 = pil_from_b64(fotos[idx1]['imagem']), pil_from_b64(fotos[idx2]['imagem'])
    with col1: st.image(img1, caption=f"Antes: {fotos[idx1]['data']}")
    with col2: st.image(img2, caption=f"Depois: {fotos[idx2]['data']}")
    alpha = st.slider("Sobrepor (0=antes, 1=depois)", 0.0, 1.0, 0.5)
    blended = overlay_blend(img1, img2, alpha)
    st.image(blended, caption=f"Sobreposição (alpha={alpha})", use_column_width=True)
    with st.expander("Análise Técnica (Métricas de Similaridade)"): st.json(compare_images_metric(img1, img2))


def render_medidas():
    st.title("📏 Medidas Corporais")
    with st.form("form_med"):
        tipo = st.selectbox("Tipo", ['Cintura', 'Quadril', 'Braço', 'Coxa', 'Peito'])
        valor = st.number_input("Valor (cm)", 10.0, 300.0, 80.0, 0.1)
        data_medida = st.date_input("Data", datetime.now().date())
        if st.form_submit_button("Salvar medida"):
            st.session_state.medidas.append({'tipo': tipo, 'valor': float(valor), 'data': data_medida.isoformat()})
            uid = st.session_state.get('user_uid');
            if uid: salvar_dados_usuario_firebase(uid)
            st.success("Medida salva.")
    if st.session_state.get('medidas'):
        dfm = pd.DataFrame(st.session_state['medidas']);
        dfm['data'] = pd.to_datetime(dfm['data'])
        fig = px.line(dfm, x='data', y='valor', color='tipo', markers=True)
        st.plotly_chart(fig, use_container_width=True)


def render_planner():
    st.title("🗓️ Planejamento Semanal")
    dados = st.session_state.get('dados_usuario') or {}
    dias_sem = dados.get('dias_semana', 3)
    if dias_sem: st.write(f"Você planeja treinar {dias_sem} dias por semana.")
    hoje = date.today();
    treinou = set(st.session_state.get('frequencia', []))
    eventos = [{'title': 'Treino 💪', 'start': d.isoformat(), 'allDay': True} for d in treinou]
    try:
        from streamlit_calendar import calendar
        calendar(events=eventos, options={"initialView": "dayGridMonth", "height": "600px"})
    except ImportError:
        st.warning("Instale `streamlit-calendar` para uma melhor visualização.")
        st.write([e['start'] for e in eventos])


def render_metas():
    st.title("🎯 Metas")
    with st.form("form_meta"):
        descricao = st.text_input("Descrição da Meta (ex: Correr 5km)")
        prazo = st.date_input("Prazo", min_value=datetime.now().date())
        if st.form_submit_button("Adicionar Meta"):
            st.session_state.metas.append(
                {'descricao': descricao, 'prazo': prazo.isoformat(), 'criada_em': iso_now(), 'concluida': False})
            uid = st.session_state.get('user_uid');
            if uid: salvar_dados_usuario_firebase(uid)
            st.success("Meta adicionada.")
    for i, m in enumerate(st.session_state.get('metas', [])):
        col1, col2 = st.columns([4, 1])
        with col1:
            status = "✔️" if m.get('concluida') else "⏳"
            st.write(f"{status} **{m['descricao']}** (Prazo: {m['prazo']})")
        with col2:
            if not m.get('concluida'):
                if st.button("✅ Concluir", key=f"conq_{i}"):
                    st.session_state['metas'][i]['concluida'] = True
                    adicionar_xp(XP_POR_META_CONCLUIDA)
                    uid = st.session_state.get('user_uid');
                    if uid: salvar_dados_usuario_firebase(uid)
                    st.success("Meta concluída!");
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
        st.metric("TMB estimada", f"{int(tmb)} kcal/dia");
        st.json(macros)


def calcular_tmb(sexo: str, peso: float, altura_cm: float, idade: int) -> float:
    if sexo.lower().startswith('m'): return 10 * peso + 6.25 * altura_cm - 5 * idade + 5
    return 10 * peso + 6.25 * altura_cm - 5 * idade - 161


def sugerir_macros(tmb: float, objetivo: str, peso_kg: float):
    calorias = tmb * 1.55
    if objetivo == 'Emagrecimento':
        calorias *= 0.8
    elif objetivo == 'Hipertrofia':
        calorias *= 1.15
    prote = 1.8 * peso_kg;
    gord = (calorias * 0.25) / 9
    carbs = (calorias - (prote * 4) - (gord * 9)) / 4
    return {'calorias (kcal)': round(calorias), 'proteina (g)': round(prote, 1), 'gordura (g)': round(gord, 1),
            'carboidratos (g)': round(carbs, 1)}


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
    payload = {k: st.session_state.get(k) for k in
               ['dados_usuario', 'frequencia', 'historico_treinos', 'metas', 'fotos_progresso', 'medidas', 'xp',
                'nivel']}
    payload['plano_treino'] = plan_to_serial(st.session_state.get('plano_treino'))
    js = json.dumps(payload, default=str, ensure_ascii=False, indent=2)
    st.download_button("📥 Baixar backup JSON", data=js, file_name="fitpro_backup.json", mime="application/json")
    if st.session_state.get('historico_treinos'):
        df = pd.DataFrame(st.session_state['historico_treinos'])
        st.download_button("📥 Exportar histórico CSV", data=df.to_csv(index=False).encode('utf-8'),
                           file_name="historico_treinos.csv", mime="text/csv")


def render_admin_panel(): pass


# ---------------------------
# NOVAS PÁGINAS: Treino ao Vivo e Rede Social
# ---------------------------
def render_treino_ao_vivo():
    st.title(f"▶️ Treino em Andamento: {st.session_state['plano_treino_ativo']['nome']}")

    plano = st.session_state['plano_treino_ativo']['df']
    logs = st.session_state['logs_treino_ativo']

    # Gerenciamento do Timer
    timer_placeholder = st.empty()
    if st.session_state.get('timer_ativo', False):
        agora = datetime.now()
        if agora < st.session_state['timer_fim']:
            restante = st.session_state['timer_fim'] - agora
            timer_placeholder.progress(restante.total_seconds() / 60, text=f"⏳ Descanso: {restante.seconds}s restantes")
            time.sleep(1)
            st.rerun()
        else:
            st.session_state['timer_ativo'] = False
            st.rerun()

    for ex_idx, exercicio_info in enumerate(plano):
        exercicio = exercicio_info['Exercício']
        series = int(exercicio_info['Séries'])
        reps = exercicio_info['Repetições']
        descanso_str = exercicio_info['Descanso']
        descanso_seg = int(re.search(r'\d+', descanso_str).group()) if re.search(r'\d+', descanso_str) else 60

        st.subheader(exercicio)

        for serie_idx in range(1, series + 1):
            log_key = f"{ex_idx}-{serie_idx}"
            log_entry = logs.get(log_key, {'reps': 0, 'peso': 0.0, 'feito': False})

            cols = st.columns([2, 1, 1, 1])
            with cols[0]:
                feito = st.checkbox(f"Série {serie_idx}", value=log_entry['feito'], key=f"feito_{log_key}")
            with cols[1]:
                reps_input = st.number_input("Reps", value=log_entry['reps'], key=f"reps_{log_key}", min_value=0,
                                             step=1)
            with cols[2]:
                peso_input = st.number_input("Peso (kg)", value=log_entry['peso'], key=f"peso_{log_key}", min_value=0.0,
                                             step=0.5)

            # Atualiza o log e dispara o timer se necessário
            if feito and not log_entry['feito']:  # Se acabou de marcar
                logs[log_key] = {'reps': reps_input, 'peso': peso_input, 'feito': True}
                st.session_state['logs_treino_ativo'] = logs
                # Inicia o timer
                st.session_state['timer_ativo'] = True
                st.session_state['timer_fim'] = datetime.now() + timedelta(seconds=descanso_seg)
                st.rerun()
            elif not feito and log_entry['feito']:  # Desmarcou
                logs[log_key]['feito'] = False
                st.session_state['logs_treino_ativo'] = logs
            else:  # Apenas atualiza reps/peso
                logs[log_key]['reps'] = reps_input
                logs[log_key]['peso'] = peso_input

    st.markdown("---")
    col1, col2 = st.columns(2)
    if col1.button("✅ Finalizar Treino", use_container_width=True):
        with st.spinner("Salvando seu treino..."):
            data_treino = date.today()
            nome_treino = st.session_state['plano_treino_ativo']['nome']

            for log_key, log_data in logs.items():
                if log_data['feito']:
                    ex_idx, serie_idx = map(int, log_key.split('-'))
                    exercicio_info = plano[ex_idx]

                    novo = {
                        'data': data_treino, 'tipo': nome_treino, 'exercicio': exercicio_info['Exercício'],
                        'series': 1, 'reps': log_data['reps'], 'peso': log_data['peso'],
                        'volume': log_data['reps'] * log_data['peso'], 'observacoes': 'Registrado via Treino Ao Vivo',
                        'timestamp': iso_now()
                    }
                    st.session_state.historico_treinos.append(novo)

            frequencia = set(st.session_state.frequencia)
            frequencia.add(data_treino)
            st.session_state.frequencia = sorted(list(frequencia))

            adicionar_xp(XP_POR_TREINO)
            atualizar_streak(data_treino)
            verificar_e_conceder_conquistas()

            uid = st.session_state.get('user_uid')
            nome_usuario = st.session_state.get('usuario_logado')
            if uid:
                salvar_dados_usuario_firebase(uid)
                criar_post_feed(uid, nome_usuario, 'treino_concluido', f"Completou o treino: {nome_treino} 💪")

            st.session_state['treino_ativo'] = False
            st.session_state['plano_treino_ativo'] = None
            st.session_state['logs_treino_ativo'] = {}
        st.success("Treino finalizado e salvo com sucesso!")
        st.balloons()
        time.sleep(2)
        st.rerun()

    if col2.button("❌ Cancelar Treino", type="secondary", use_container_width=True):
        st.session_state['treino_ativo'] = False
        st.session_state['plano_treino_ativo'] = None
        st.session_state['logs_treino_ativo'] = {}
        st.warning("Treino cancelado.")
        time.sleep(1)
        st.rerun()


@st.cache_data(ttl=60)
def get_all_users():
    users = db.collection('usuarios').stream()
    return {user.id: user.to_dict().get('username', 'Usuário Anônimo') for user in users}


@st.cache_data(ttl=60)
def get_feed_posts(seguindo_uids: list):
    # Em um app real, a query seria mais complexa para escalar.
    # Por simplicidade, buscamos os últimos 50 e filtramos.
    if not seguindo_uids:
        return []
    posts_ref = db.collection('feed').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(50)
    posts = posts_ref.stream()
    feed_items = []
    for post in posts:
        post_data = post.to_dict()
        if post_data.get('uid_autor') in seguindo_uids:
            post_data['id'] = post.id
            feed_items.append(post_data)
    return feed_items


def render_rede_social():
    st.title("🌐 Rede Social")
    meu_uid = st.session_state.get('user_uid')

    tab_feed, tab_usuarios = st.tabs(["Meu Feed", "Encontrar Usuários"])

    with tab_feed:
        st.subheader("Atividades recentes de quem você segue")
        seguindo_uids = st.session_state.get('seguindo', [])
        if not seguindo_uids:
            st.info("Você ainda não segue ninguém. Encontre usuários na aba ao lado!")
        else:
            feed_items = get_feed_posts(seguindo_uids + [meu_uid])  # Inclui seus próprios posts
            if not feed_items:
                st.info("Nenhuma atividade recente no seu feed.")

            for post in feed_items:
                with st.container(border=True):
                    ts = post['timestamp'].strftime("%d/%m/%Y às %H:%M") if isinstance(post.get('timestamp'),
                                                                                       datetime) else ''
                    st.markdown(f"**{post.get('nome_autor', 'Usuário')}** `({ts})`")
                    st.write(post.get('texto', ''))

                    # Sistema de Curtidas
                    curtidas = post.get('curtidas', [])
                    curtido_por_mim = meu_uid in curtidas

                    btn_text = f"❤️ Curtir ({len(curtidas)})" if not curtido_por_mim else f"💙 Curtido ({len(curtidas)})"
                    if st.button(btn_text, key=f"like_{post['id']}"):
                        post_ref = db.collection('feed').document(post['id'])
                        if curtido_por_mim:
                            post_ref.update({'curtidas': firestore.ArrayRemove([meu_uid])})
                        else:
                            post_ref.update({'curtidas': firestore.ArrayUnion([meu_uid])})
                        st.cache_data.clear()  # Limpa o cache para recarregar os posts
                        st.rerun()

    with tab_usuarios:
        st.subheader("Encontre e siga outros atletas")
        all_users = get_all_users()

        for uid, username in all_users.items():
            if uid == meu_uid: continue  # Não mostrar a si mesmo

            cols = st.columns([3, 1])
            cols[0].write(f"**{username}**")

            is_following = uid in st.session_state.get('seguindo', [])
            btn_label = "Deixar de Seguir" if is_following else "Seguir"
            if cols[1].button(btn_label, key=f"follow_{uid}", type="primary" if not is_following else "secondary"):
                if is_following:
                    st.session_state['seguindo'].remove(uid)
                else:
                    st.session_state['seguindo'].append(uid)

                # Salvar alteração no Firestore
                salvar_dados_usuario_firebase(meu_uid)
                st.success(f"Agora você {'não segue mais' if is_following else 'está seguindo'} {username}!")
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
                    cookies['user_uid'] = '';
                    cookies.save()
            except Exception as e:
                st.error(f"Erro ao tentar login automático: {e}")

    if not st.session_state.get('usuario_logado'):
        render_auth()
    else:
        render_main()


if __name__ == "__main__":
    run()