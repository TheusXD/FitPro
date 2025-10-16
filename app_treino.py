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
import io
import json
import time
import base64
import logging
import requests  # Importação necessária para buscar GIFs
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from PIL import Image, ImageChops, ImageFilter, ImageStat
from streamlit_cookies_manager import CookieManager
import requests
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
def find_exercise_gif(exercise_name: str) -> Optional[str]:
    """Busca por um GIF de exercício e retorna a URL."""
    try:
        search_term = f"{exercise_name} exercise animated gif"
        params = {"q": search_term, "key": "LIVDSRZULELA", "limit": 1}
        response = requests.get("https://g.tenor.com/v1/search", params=params)
        response.raise_for_status()
        results = response.json()
        if results['results']:
            return results['results'][0]['media'][0]['gif']['url']
    except Exception:
        return None
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
    # Pernas
    'Agachamento com Barra': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra',
                              'restricoes': ['Lombar', 'Joelhos']},
    'Agachamento com Halteres': {'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres',
                                 'restricoes': ['Joelhos']},
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
    'Agachamento com Barra': 'Leg Press 45°',
    'Stiff com Halteres': 'Mesa Flexora',
    'Remada Curvada com Barra': 'Remada Sentada (máquina)',
    'Desenvolvimento Militar com Barra': 'Desenvolvimento com Halteres (sentado)',
    'Supino Reto com Barra': 'Supino Reto com Halteres',
    'Tríceps Testa': 'Tríceps Pulley',
    'Rosca Direta com Barra': 'Rosca Direta com Halteres',
    'Flexão de Braço': 'Supino Reto com Halteres',
    'Elevação de Pernas': 'Prancha'
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
                    if substituto and substituto not in candidatos_validos:
                        candidatos_validos.append(substituto)
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
    # --- LÓGICA ALTERADA AQUI ---
    if dias <= 2:  # Agora, a verificação é apenas pelo número de dias
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

    # Pega os dados do estado da sessão
    plano_atual = st.session_state['current_workout_plan']
    idx_atual = st.session_state['current_exercise_index']
    exercicio_atual = plano_atual[idx_atual]

    nome_exercicio = exercicio_atual['Exercício']
    series_str = exercicio_atual['Séries']

    try:
        num_series = int(str(series_str).split('-')[0])
    except:
        num_series = 3

    # --- Barra de Progresso e Timer ---
    progresso = (idx_atual + 1) / len(plano_atual)
    col_prog, col_timer = st.columns(2)
    col_prog.progress(progresso, text=f"Exercício {idx_atual + 1} de {len(plano_atual)}")
    timer_placeholder = col_timer.empty()  # Placeholder para o timer

    # --- Container do Exercício Atual ---
    with st.container(border=True):
        col_gif, col_details = st.columns([2, 3])
        with col_gif:
            gif_url = find_exercise_gif(nome_exercicio)
            if gif_url:
                st.image(gif_url)
            else:
                st.text("GIF indisponível")
        with col_details:
            st.header(nome_exercicio)
            st.markdown(
                f"**Séries:** `{exercicio_atual['Séries']}` | **Repetições:** `{exercicio_atual['Repetições']}`")
            st.markdown(f"**Descanso:** `{exercicio_atual['Descanso']}`")

    st.subheader("Registre suas séries")

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

    # --- Checklist de Séries ---
    for i in range(num_series):
        set_key = f"set_{idx_atual}_{i}"
        if set_key not in st.session_state:
            st.session_state[set_key] = {'completed': False, 'weight': 0.0, 'reps': 0}
        set_info = st.session_state[set_key]

        cols = st.columns([1, 2, 2, 1])

        # Desabilita o checkbox se o usuário estiver descansando (e a série não for a que iniciou o descanso)
        disable_checkbox = is_resting and not set_info['completed']

        completed = cols[0].checkbox(f"Série {i + 1}", value=set_info['completed'], key=f"check_{set_key}",
                                     disabled=disable_checkbox)

        if completed and not set_info['completed']:  # Se acabou de marcar
            if is_resting:
                st.warning("Termine seu descanso antes de marcar a próxima série!")
                set_info['completed'] = False  # Reverte a ação
            else:
                set_info['completed'] = True
                descanso_str = exercicio_atual.get('Descanso', '60s')
                try:
                    rest_seconds = int(re.search(r'\d+', descanso_str).group())
                except:
                    rest_seconds = 60

                st.session_state.rest_timer_end = time.time() + rest_seconds
                st.session_state.workout_log.append({
                    'data': date.today().isoformat(), 'exercicio': nome_exercicio, 'series': i + 1,
                    'peso': set_info['weight'], 'reps': set_info['reps'], 'timestamp': iso_now()
                })
                st.rerun()

        if not set_info['completed']:
            set_info['weight'] = cols[1].number_input("Peso (kg)", key=f"weight_{set_key}",
                                                      value=float(set_info['weight']), format="%.1f",
                                                      disabled=is_resting)
            set_info['reps'] = cols[2].number_input("Reps", key=f"reps_{set_key}", value=int(set_info['reps']),
                                                    disabled=is_resting)
        else:
            cols[1].write(f"Peso: **{set_info['weight']} kg**")
            cols[2].write(f"Reps: **{set_info['reps']}**")

    st.markdown("---")

    # --- Botões de Navegação do Treino ---
    all_sets_done = all(
        st.session_state.get(f"set_{idx_atual}_{i}", {}).get('completed', False) for i in range(num_series))

    nav_cols = st.columns([1, 1, 1])
    with nav_cols[1]:
        if all_sets_done:
            if idx_atual < len(plano_atual) - 1:
                if st.button("Próximo Exercício →", use_container_width=True, type="primary"):
                    st.session_state['current_exercise_index'] += 1;
                    st.rerun()
            else:
                if st.button("✅ Finalizar Treino", use_container_width=True, type="primary"):
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
    with nav_cols[2]:
        if st.button("❌ Desistir do Treino", use_container_width=True):
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
            if not hp or hp[-1].get('peso') != peso:
                hp.append({'data': iso_now(), 'peso': peso})
                st.session_state['historico_peso'] = hp
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
    if not plano or all(isinstance(df, list) and not df for df in plano.values()):
        st.info("Você ainda não tem um plano de treino. Vá para a página 'Questionário' para gerar o seu primeiro!")
        return

    dados = st.session_state.get('dados_usuario') or {}
    st.info(
        f"Este plano foi criado para um atleta **{dados.get('nivel', '')}** treinando **{dados.get('dias_semana', '')}** dias por semana com foco em **{dados.get('objetivo', '')}**.")
    st.markdown("---")

    for nome_treino, df_treino_dict in plano.items():
        # [CORREÇÃO APLICADA AQUI]
        # 1. Primeiro, converta para DataFrame
        df_treino = pd.DataFrame(df_treino_dict)

        # 2. Agora, verifique se o DataFrame está vazio usando .empty
        if df_treino.empty:
            continue

        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader(nome_treino)
            st.caption(f"{len(df_treino)} exercícios")
        with col2:
            if st.button("▶️ Iniciar Treino", key=f"start_{nome_treino}", use_container_width=True, type="primary"):
                st.session_state.update({
                    'workout_in_progress': True,
                    'current_workout_plan': df_treino.to_dict('records'),
                    'current_exercise_index': 0,
                    'workout_log': [],
                    'set_timers': {}
                })
                st.rerun()

        for index, row in df_treino.iterrows():
            exercicio, series, repeticoes, descanso = row['Exercício'], row['Séries'], row['Repetições'], row[
                'Descanso']
            with st.expander(f"**{exercicio}** | {series} Séries x {repeticoes} Reps"):
                col1, col2 = st.columns([2, 3])
                with col1:
                    gif_url = find_exercise_gif(exercicio)
                    if gif_url:
                        st.image(gif_url, caption=f"Execução de {exercicio}")
                    else:
                        st.info("Guia visual indisponível.")
                with col2:
                    st.markdown("##### 📋 **Instruções**")
                    st.markdown(
                        f"- **Séries:** `{series}`\n- **Repetições:** `{repeticoes}`\n- **Descanso:** `{descanso}`")
                    st.markdown("---")
                    st.write(f"**Grupo Muscular:** {EXERCICIOS_DB.get(exercicio, {}).get('grupo', 'N/A')}")
                    st.write(f"**Equipamento:** {EXERCICIOS_DB.get(exercicio, {}).get('equipamento', 'N/A')}")
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
                fotos = st.session_state.get('fotos_progresso', [])
                fotos.append({'data': data_foto.isoformat(), 'peso': float(peso_foto), 'imagem': b64, 'nota': nota,
                              'timestamp': iso_now()})
                st.session_state['fotos_progresso'] = fotos
                uid = st.session_state.get('user_uid')
                if uid: salvar_dados_usuario_firebase(uid)
                st.success("Foto salva.");
                st.rerun()
    st.subheader("Galeria")
    fotos = sorted(st.session_state.get('fotos_progresso', []), key=lambda x: x.get('data', ''), reverse=True)
    if not fotos: st.info("Nenhuma foto ainda."); return
    for i, f in enumerate(fotos):
        c1, c2, c3 = st.columns([1, 3, 1])
        with c1:
            try:
                st.image(base64.b64decode(f['imagem']), width=140)
            except Exception:
                st.write("Imagem inválida")
        with c2:
            st.write(f"📅 {f.get('data')}  ⚖️ {f.get('peso')}kg")
            if f.get('nota'): st.write(f"📝 {f.get('nota')}")
        with c3:
            if st.button("🗑️ Excluir", key=f"del_{i}", use_container_width=True):
                confirm_delete_photo_dialog(i, st.session_state.get('user_uid'))
    if st.session_state.get('confirm_excluir_foto'):
        st.warning("Deseja realmente excluir esta foto?")
        ca, cb = st.columns(2)
        with ca:
            if st.button("❌ Cancelar"):
                st.session_state['confirm_excluir_foto'] = False;
                st.session_state['foto_a_excluir'] = None;
                st.rerun()
        with cb:
            if st.button("✅ Confirmar exclusão"):
                idx = st.session_state.get('foto_a_excluir')
                fotos = st.session_state.get('fotos_progresso', [])
                if idx is not None and idx < len(fotos):
                    del fotos[idx]
                    st.session_state['fotos_progresso'] = fotos
                    uid = st.session_state.get('user_uid')
                    if uid: salvar_dados_usuario_firebase(uid)
                    st.success("Foto excluída.")
                st.session_state['confirm_excluir_foto'] = False;
                st.session_state['foto_a_excluir'] = None;
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
    with st.form("form_med"):
        tipo = st.selectbox("Tipo", ['Cintura', 'Quadril', 'Braço', 'Coxa', 'Peito'])
        valor = st.number_input("Valor (cm)", 10.0, 300.0, 80.0, 0.1)
        data = st.date_input("Data", datetime.now().date())
        if st.form_submit_button("Salvar medida"):
            medidas = st.session_state.get('medidas', [])
            medidas.append({'tipo': tipo, 'valor': float(valor), 'data': data.isoformat()})
            st.session_state['medidas'] = medidas
            uid = st.session_state.get('user_uid')
            if uid: salvar_dados_usuario_firebase(uid)
            st.success("Medida salva.")
    if st.session_state.get('medidas'):
        dfm = pd.DataFrame(st.session_state['medidas'])
        dfm['data'] = pd.to_datetime(dfm['data'])
        fig = px.line(dfm, x='data', y='valor', color='tipo', markers=True)
        st.plotly_chart(fig, use_container_width=True)


def render_planner():
    st.title("🗓️ Planejamento Semanal")
    dados = st.session_state.get('dados_usuario') or {}
    dias_sem = dados.get('dias_semana', 3)
    st.write("Sugestão de dias (0=Seg):", suggest_days(dias_sem))
    hoje = datetime.now().date()
    dias = [hoje + timedelta(days=i) for i in range(14)]
    treinou = set(st.session_state.get('frequencia', []))
    df = pd.DataFrame({'data': dias, 'treinou': [1 if d in treinou else 0 for d in dias]})
    df['data'] = pd.to_datetime(df['data'])
    df['weekday'] = df['data'].dt.weekday
    df['week'] = df['data'].dt.isocalendar().week
    try:
        pivot = df.pivot(index='week', columns='weekday', values='treinou').fillna(0)
        fig = px.imshow(pivot, labels=dict(x='weekday', y='week', color='treinou'), text_auto=True)
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.table(df)


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
    payload = {k: st.session_state.get(k) for k in
               ['dados_usuario', 'frequencia', 'historico_treinos', 'metas', 'fotos_progresso', 'medidas']}
    payload['plano_treino'] = plan_to_serial(st.session_state.get('plano_treino'))
    js = json.dumps(payload, default=str, ensure_ascii=False)
    st.download_button("📥 Baixar backup JSON", data=js, file_name="fitpro_backup.json", mime="application/json")
    if st.session_state.get('historico_treinos'):
        df = pd.DataFrame(st.session_state['historico_treinos'])
        st.download_button("📥 Exportar histórico CSV", data=df.to_csv(index=False), file_name="historico_treinos.csv",
                           mime="text/csv")
    if st.button("Criar backup na coleção 'backups'"):
        uid = st.session_state.get('user_uid')
        if uid:
            db.collection('backups').add({'uid': uid, 'payload': payload, 'created': datetime.now()})
            st.success("Backup criado na coleção 'backups'.")


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