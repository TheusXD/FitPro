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
import uuid
logout_key = f"logout_{uuid.uuid4().hex[:8]}"
nav_key = f"nav_select_{uuid.uuid4().hex[:8]}"
theme_key = f"theme_select_{uuid.uuid4().hex[:8]}"
notify_key = f"notify_check_{uuid.uuid4().hex[:8]}"
error_key = f"error_btn_{uuid.uuid4().hex[:8]}"

# Adicione no topo do arquivo com os outros imports
import uuid



import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from PIL import Image, ImageChops, ImageFilter, ImageStat
from streamlit_cookies_manager import CookieManager

from datetime import datetime, date, timedelta, timezone
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


def verificar_dataframe_valido(df):
    """
    Verifica se um DataFrame é válido para uso
    """
    if df is None:
        return False
    if not isinstance(df, pd.DataFrame):
        return False
    if df.empty:
        return False
    if 'Exercício' not in df.columns:
        return False
    return True


def verificar_plano_valido(plano):
    """
    Verifica se o plano de treino é válido
    """
    if not plano or not isinstance(plano, dict):
        return False

    dias_validos = 0
    for nome_treino, treino_data in plano.items():
        if treino_data is not None:
            if isinstance(treino_data, pd.DataFrame):
                if verificar_dataframe_valido(treino_data):
                    dias_validos += 1
            elif isinstance(treino_data, list):
                if len(treino_data) > 0:
                    dias_validos += 1

    return dias_validos > 0

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

st.markdown("""
<style>
    /* Remove o padding padrão do Streamlit */
    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
    }

    /* Estiliza o selectbox de navegação */
    div[data-testid="stSelectbox"] > div {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 5px;
    }

    /* Melhora o visual dos expansores */
    .streamlit-expanderHeader {
        background-color: #f8f9fa;
        border-radius: 5px;
    }

    /* Ajusta o header principal */
    h1 {
        color: #1f77b4;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------
# Helpers
# ---------------------------
def gerar_chave_unica(prefixo=""):
    """Gera uma chave única para elementos do Streamlit"""
    import time
    import random

    # Se element_counter não existir, criar um valor baseado em timestamp
    if 'element_counter' not in st.session_state:
        st.session_state.element_counter = 0

    # Incrementar o contador
    st.session_state.element_counter += 1

    timestamp = int(time.time() * 1000)  # milissegundos
    random_suffix = random.randint(1000, 9999)
    user_suffix = st.session_state.get('user_uid', 'default')[:8] if st.session_state.get('user_uid') else 'anon'

    return f"{prefixo}_{user_suffix}_{timestamp}_{random_suffix}_{st.session_state.element_counter}"

def gerar_planejamento_automatico(dias_semana: int, plano_treino: Dict) -> Dict[str, str]:
    """
    Gera um planejamento semanal automático baseado no número de dias e plano de treino
    Retorna um dicionário com {dia_da_semana: nome_do_treino}
    """
    if not plano_treino:
        return {}

    # Mapeamento de dias da semana
    DIAS_SEMANA = {
        0: "Segunda-feira",
        1: "Terça-feira",
        2: "Quarta-feira",
        3: "Quinta-feira",
        4: "Sexta-feira",
        5: "Sábado",
        6: "Domingo"
    }

    # Nomes dos treinos disponíveis
    treinos_disponiveis = list(plano_treino.keys())

    # Estratégias de distribuição baseadas no número de dias
    if dias_semana == 1:
        # 1 dia: Full Body
        distribuicao = {0: treinos_disponiveis[0] if treinos_disponiveis else "Descanso"}

    elif dias_semana == 2:
        # 2 dias: AB (Superior/Inferior)
        distribuicao = {
            0: next((t for t in treinos_disponiveis if "Superior" in t or "Upper" in t),
                    treinos_disponiveis[0] if treinos_disponiveis else "Descanso"),
            3: next((t for t in treinos_disponiveis if "Inferior" in t or "Lower" in t),
                    treinos_disponiveis[1] if len(treinos_disponiveis) > 1 else treinos_disponiveis[
                        0] if treinos_disponiveis else "Descanso")
        }

    elif dias_semana == 3:
        # 3 dias: PPL ou ABC
        distribuicao = {}
        treinos_utilizados = set()

        for i, dia in enumerate([0, 2, 4]):  # Segunda, Quarta, Sexta
            if i < len(treinos_disponiveis):
                distribuicao[dia] = treinos_disponiveis[i]
                treinos_utilizados.add(treinos_disponiveis[i])
            else:
                # Se não há treinos suficientes, repete o último disponível
                distribuicao[dia] = treinos_disponiveis[-1] if treinos_disponiveis else "Descanso"

    elif dias_semana == 4:
        # 4 dias: Upper/Lower 2x
        distribuicao = {}
        upper_treinos = [t for t in treinos_disponiveis if
                         "Superior" in t or "Upper" in t or "Push" in t or "Peito" in t or "Costas" in t]
        lower_treinos = [t for t in treinos_disponiveis if
                         "Inferior" in t or "Lower" in t or "Pernas" in t or "Legs" in t]

        # Segunda: Upper A, Terça: Lower A, Quinta: Upper B, Sexta: Lower B
        distribuicao[0] = upper_treinos[0] if upper_treinos else treinos_disponiveis[
            0] if treinos_disponiveis else "Descanso"
        distribuicao[1] = lower_treinos[0] if lower_treinos else treinos_disponiveis[1] if len(
            treinos_disponiveis) > 1 else treinos_disponiveis[0] if treinos_disponiveis else "Descanso"
        distribuicao[3] = upper_treinos[1] if len(upper_treinos) > 1 else upper_treinos[0] if upper_treinos else \
        treinos_disponiveis[0] if treinos_disponiveis else "Descanso"
        distribuicao[4] = lower_treinos[1] if len(lower_treinos) > 1 else lower_treinos[0] if lower_treinos else \
        treinos_disponiveis[1] if len(treinos_disponiveis) > 1 else treinos_disponiveis[
            0] if treinos_disponiveis else "Descanso"

    elif dias_semana >= 5:
        # 5+ dias: Distribuição inteligente com descanso estratégico
        distribuicao = {}
        treinos_cycle = cycle(treinos_disponiveis)

        # Distribui os treinos ao longo da semana, evitando 3 dias consecutivos
        dias_treino = []
        for i in range(dias_semana):
            if i == 0:
                dias_treino.append(0)  # Segunda
            elif i == 1:
                dias_treino.append(1)  # Terça
            elif i == 2:
                dias_treino.append(3)  # Quinta
            elif i == 3:
                dias_treino.append(4)  # Sexta
            elif i == 4:
                dias_treino.append(5)  # Sábado
            elif i == 5:
                dias_treino.append(6)  # Domingo
            else:
                # Para mais de 6 dias, preenche os dias restantes
                for dia in range(7):
                    if dia not in dias_treino:
                        dias_treino.append(dia)
                        break

        for dia in sorted(dias_treino):
            distribuicao[dia] = next(treinos_cycle)

    # Preenche os dias não utilizados com "Descanso"
    planejamento_completo = {}
    for dia_num, dia_nome in DIAS_SEMANA.items():
        if dia_num in distribuicao:
            planejamento_completo[dia_nome] = distribuicao[dia_num]
        else:
            planejamento_completo[dia_nome] = "Descanso"

    return planejamento_completo

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

# ==========================================================
# =                INÍCIO - SISTEMA DE GAMIFICAÇÃO           =
# ==========================================================

def verificar_reset_semanal(user_uid: str):
    """
    Verifica se uma nova semana (Segunda-feira) começou e reseta o 'xp_semanal'.
    Isso é um "cron job" que roda quando o usuário abre o app.
    """
    if not user_uid or user_uid == 'demo-uid':
        return

    try:
        user_ref = db.collection('usuarios').document(user_uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            return

        user_data = user_doc.to_dict()

        # ==================== CORREÇÃO AQUI ====================
        # Pega a data/hora atual EM UTC (offset-aware)
        hoje = datetime.now(timezone.utc)
        # =======================================================

        # Encontra a última Segunda-feira
        inicio_da_semana = hoje - timedelta(days=hoje.weekday())
        inicio_da_semana = inicio_da_semana.replace(hour=0, minute=0, second=0, microsecond=0)
        # 'inicio_da_semana' agora também está em UTC (aware)

        ultima_verificacao = user_data.get('ultima_verificacao_semanal')

        # A comparação agora funciona (aware vs aware)
        if not ultima_verificacao or ultima_verificacao < inicio_da_semana:
            # Reseta o XP Semanal no doc do usuário E no leaderboard
            batch = db.batch()

            # Reseta no /usuarios/{uid}
            batch.update(user_ref, {
                'xp_semanal': 0,
                'ultima_verificacao_semanal': hoje  # Salva a data 'aware'
            })

            # Reseta no /leaderboard_semanal/{uid}
            leaderboard_ref = db.collection('leaderboard_semanal').document(user_uid)
            batch.set(leaderboard_ref, {
                'xp_semanal': 0,
                'username': user_data.get('username', 'Usuário Anônimo'),
                'user_uid': user_uid
            }, merge=True)

            batch.commit()
            st.toast("🏆 O ranking semanal foi reiniciado!")

    except Exception as e:
        # Silencioso para não atrapalhar o usuário
        print(f"Erro no reset semanal: {e}")


def calcular_xp_ganho(dados_treino: dict) -> int:
    """Calcula o XP ganho por um único treino registrado."""
    xp = 0

    # 1. Base por treino
    xp += 100  # Pontos por registrar um treino

    # 2. Bônus de Intensidade
    intensidade = dados_treino.get('intensidade', 'Moderada')
    if intensidade == 'Intensa':
        xp += 25
    elif intensidade == 'Muito Intensa':
        xp += 50

    # 3. Bônus de Duração
    duracao = dados_treino.get('duracao', 60)
    if duracao > 75:
        xp += 20  # Bônus por treino longo

    # TODO: Bônus por PR (Recorde Pessoal)
    # Isso exigiria uma lógica mais complexa para verificar o 'workout_log'
    # if treino_resultou_em_pr:
    #    xp += 150

    return xp


def atualizar_xp_usuario(user_uid: str, username: str, xp_ganho: int):
    """
    Atualiza o XP no Firestore (no doc 'usuarios' e no 'leaderboard_semanal')
    usando firestore.Increment para segurança atômica.
    """
    if not user_uid or user_uid == 'demo-uid' or xp_ganho == 0:
        return

    try:
        user_ref = db.collection('usuarios').document(user_uid)
        leaderboard_ref = db.collection('leaderboard_semanal').document(user_uid)

        batch = db.batch()

        # 1. Atualiza o /usuarios/{uid}
        batch.update(user_ref, {
            'xp_total': firestore.Increment(xp_ganho),
            'xp_semanal': firestore.Increment(xp_ganho)
        })

        # 2. Atualiza (ou cria) o /leaderboard_semanal/{uid}
        # 'merge=True' é crucial: cria se não existe, atualiza se existe.
        batch.set(leaderboard_ref, {
            'username': username,
            'user_uid': user_uid,
            'xp_semanal': firestore.Increment(xp_ganho)
        }, merge=True)

        batch.commit()
        st.toast(f"🎉 +{xp_ganho} XP!")

    except Exception as e:
        st.error(f"Erro ao atualizar XP: {e}")


def verificar_novas_conquistas(user_uid: str):
    """
    Verifica se o usuário ganhou novas conquistas (badges)
    e as salva na subcoleção /usuarios/{uid}/conquistas
    """
    if not user_uid or user_uid == 'demo-uid':
        return

    try:
        user_ref = db.collection('usuarios').document(user_uid)
        user_data = user_ref.get().to_dict()
        if not user_data:
            return

        conquistas_ref = user_ref.collection('conquistas')
        # Pega IDs das conquistas que o usuário já tem
        existing_conquistas_ids = [doc.id for doc in conquistas_ref.stream()]

        batch_conquistas = db.batch()
        novas_conquistas_ganhas = []  # Para mostrar notificação

        # --- Definir as regras das conquistas ---

        historico_treinos = user_data.get('historico_treinos', [])
        frequencia = user_data.get('frequencia', [])
        fotos = user_data.get('fotos_progresso', [])

        # Conquista 1: Primeiro Treino
        if len(historico_treinos) >= 1 and 'treino_1' not in existing_conquistas_ids:
            novas_conquistas_ganhas.append("Começou a Jornada")
            conquista_ref_doc = conquistas_ref.document('treino_1')
            batch_conquistas.set(conquista_ref_doc, {
                'nome': 'Começou a Jornada',
                'descricao': 'Registrou seu primeiro treino.',
                'data': iso_now()
            })

        # Conquista 2: 10 Treinos
        if len(historico_treinos) >= 10 and 'treino_10' not in existing_conquistas_ids:
            novas_conquistas_ganhas.append("Novato de Ferro")
            conquista_ref_doc = conquistas_ref.document('treino_10')
            batch_conquistas.set(conquista_ref_doc, {
                'nome': 'Novato de Ferro',
                'descricao': 'Completou 10 treinos.',
                'data': iso_now()
            })

        # Conquista 3: 50 Treinos
        if len(historico_treinos) >= 50 and 'treino_50' not in existing_conquistas_ids:
            novas_conquistas_ganhas.append("Veterano")
            conquista_ref_doc = conquistas_ref.document('treino_50')
            batch_conquistas.set(conquista_ref_doc, {
                'nome': 'Veterano',
                'descricao': 'Completou 50 treinos.',
                'data': iso_now()
            })

        # Conquista 4: Streak de 7 dias
        streak = calcular_streak(frequencia)  # Sua função existente
        if streak >= 7 and 'streak_7' not in existing_conquistas_ids:
            novas_conquistas_ganhas.append("Implacável")
            conquista_ref_doc = conquistas_ref.document('streak_7')
            batch_conquistas.set(conquista_ref_doc, {
                'nome': 'Implacável',
                'descricao': 'Treinou por 7 dias seguidos.',
                'data': iso_now()
            })

        # Conquista 5: Primeira Foto
        if len(fotos) >= 1 and 'foto_1' not in existing_conquistas_ids:
            novas_conquistas_ganhas.append("O Início")
            conquista_ref_doc = conquistas_ref.document('foto_1')
            batch_conquistas.set(conquista_ref_doc, {
                'nome': 'O Início',
                'descricao': 'Tirou sua primeira foto de progresso.',
                'data': iso_now()
            })

        # --- Salvar no Firebase ---
        if novas_conquistas_ganhas:
            batch_conquistas.commit()
            st.balloons()
            for nome_conquista in novas_conquistas_ganhas:
                st.success(f"🏆 Nova Conquista: {nome_conquista}!")

    except Exception as e:
        print(f"Erro ao verificar conquistas: {e}")


@st.cache_data(ttl=300)  # Cache de 5 minutos
def get_leaderboard_data(collection_name: str, uids_list: Optional[List] = None, limit: int = 100):
    """
    Busca dados do leaderboard.
    Se 'uids_list' for fornecido, busca apenas esses UIDs (para ranking de amigos).
    Senão, busca o ranking global.
    """
    try:
        query_ref = db.collection(collection_name)

        if uids_list:
            if not uids_list:  # Se a lista de amigos estiver vazia
                return []
            # Firestore 'in' query só aceita listas de até 30
            ranking_data = []
            for i in range(0, len(uids_list), 30):
                chunk = uids_list[i:i + 30]

                # ==================== CORREÇÃO AQUI (NOVO FILTRO) ====================
                # Trocamos .where('user_uid', 'in', chunk)
                docs = query_ref.where(
                    filter=firestore.FieldFilter('user_uid', 'in', chunk)
                ).stream()
                # =====================================================================

                ranking_data.extend([doc.to_dict() for doc in docs])

            # Ordena no Python, pois o 'in' não pode ser combinado com 'order_by'
            ranking_data.sort(key=lambda x: x.get('xp_semanal', 0), reverse=True)
            return ranking_data

        else:  # Ranking Global
            docs = query_ref.order_by('xp_semanal', direction=firestore.Query.DESCENDING).limit(limit).stream()
            return [doc.to_dict() for doc in docs]

    except Exception as e:
        st.error(f"Erro ao carregar ranking: {e}")
        return []


def render_ranking():
    """Renderiza a página de Ranking (Leaderboard)."""
    st.title("🏆 Ranking Semanal")
    st.info("Veja quem mais treinou esta semana! O ranking é baseado no XP ganho e reinicia toda Segunda-feira.")

    user_uid = st.session_state.get('user_uid')

    # 1. Ranking de Amigos (Baseado em quem você segue)
    st.subheader("👥 Ranking de Amigos")
    following_list = get_following_list(user_uid) + [user_uid]  # Adiciona você mesmo

    amigos_ranking = get_leaderboard_data('leaderboard_semanal', uids_list=following_list)

    if amigos_ranking:
        df_amigos = pd.DataFrame(amigos_ranking)
        df_amigos = df_amigos.sort_values(by='xp_semanal', ascending=False)
        st.dataframe(df_amigos[['username', 'xp_semanal']],
                     column_config={
                         "username": "Usuário",
                         "xp_semanal": "XP Semanal"
                     }, hide_index=True, use_container_width=True)
    else:
        st.info("Você e seus amigos ainda não pontuaram esta semana. Siga mais pessoas em 'Buscar Usuários'!")

    # 2. Ranking Global (VIP)
    st.subheader("🌎 Ranking Global (VIP)")
    user_role = st.session_state.get('role', 'free')

    if user_role in ['vip', 'admin']:
        global_ranking = get_leaderboard_data('leaderboard_semanal', limit=100)

        if global_ranking:
            df_global = pd.DataFrame(global_ranking)
            st.dataframe(df_global[['username', 'xp_semanal']],
                         column_config={
                             "username": "Usuário",
                             "xp_semanal": "XP Semanal"
                         }, hide_index=True, use_container_width=True)
        else:
            st.info("O ranking global está vazio.")
    else:
        # Se não for VIP, mostre o CTA
        render_vip_cta(
            title="Desbloqueie o Ranking Global!",
            text="Veja como você se compara a todos os atletas da plataforma.",
            button_text="Quero competir no Global (VIP)",
            key_prefix="cta_ranking"
        )


# ==========================================================
# =                  FIM - SISTEMA DE GAMIFICAÇÃO          =
# ==========================================================

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
    """Garante que todos os valores padrão da sessão existam"""
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
        'role': 'free',
        'notificacoes': [],
        'settings': {'theme': 'light', 'notify_on_login': True},
        'offline_mode': False,
        'confirm_excluir_foto': False,
        'foto_a_excluir': None,
        'workout_in_progress': False,
        'current_workout_plan': None,
        'current_exercise_index': 0,
        'workout_log': [],
        'rest_timer_end': None,
        'warmup_in_progress': False,
        'cooldown_in_progress': False,
        'current_routine_exercise_index': 0,
        'routine_timer_end': None,
        'timer_finished_flag': False,
        'confirm_reset': False,
        'selected_page': 'Dashboard',
        'element_counter': 0,  # ⬅️ ADICIONE ESTA LINHA
    }

    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
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

# ---------------------------
# Banco de Exercícios Expandido
# ---------------------------
WARMUP_ROUTINE_VIP_MOBILITY = [
    {"nome": "Gato-Camelo (Mobilidade Coluna)", "duracao_s": 60, "descricao": "Em quatro apoios, alterne arquear e arredondar a coluna."},
    {"nome": "Rotação Torácica (Quatro Apoios)", "duracao_s": 45, "descricao": "Em quatro apoios, leve uma mão à nuca e gire o tronco, apontando o cotovelo para o teto."},
    {"nome": "Círculos de Quadril (Em pé)", "duracao_s": 60, "descricao": "Em pé, mãos na cintura, faça círculos amplos com o quadril."},
    {"nome": "Alongamento Maior Alongamento do Mundo", "duracao_s": 60, "descricao": "Posição de afundo, mão interna no chão, rotacione o tronco elevando o braço externo."},
    {"nome": "Agachamento Cossaco (Mobilidade)", "duracao_s": 60, "descricao": "Pernas afastadas, transfira o peso para um lado, agachando lateralmente enquanto a outra perna estende."},
]

COOLDOWN_ROUTINE_VIP_YOGA = [
    {"nome": "Postura da Criança (Yoga Balasana)", "duracao_s": 60, "descricao": "Ajoelhado, sente-se sobre os calcanhares e incline o tronco à frente, testa no chão, braços relaxados."},
    {"nome": "Cachorro Olhando Para Baixo (Yoga Adho Mukha)", "duracao_s": 45, "descricao": "Forme um V invertido com o corpo, alongando costas e posteriores."},
    {"nome": "Alongamento Gato-Vaca Sentado (Yoga Marjaryasana/Bitilasana)", "duracao_s": 60, "descricao": "Sentado, alterne arredondar e arquear a coluna."},
    {"nome": "Torção Sentado (Yoga Ardha Matsyendrasana)", "duracao_s": 30, "descricao": "Sentado, cruze uma perna sobre a outra e torça o tronco suavemente."},
    {"nome": "Alongamento Borboleta (Yoga Baddha Konasana)", "duracao_s": 45, "descricao": "Sentado, junte as solas dos pés e puxe-os para perto, deixe os joelhos caírem para os lados."},
]
ALIMENTOS_DB = {
    "Proteínas": ["Peito de Frango", "Tilápia/Peixe Branco", "Ovos", "Clara de Ovo", "Whey Protein", "Carne Vermelha Magra (Patinho, Filé Mignon)", "Tofu", "Queijo Cottage", "Iogurte Grego Natural"],
    "Carboidratos": ["Arroz Branco/Integral", "Batata Doce", "Batata Inglesa", "Mandioca (Aipim)", "Aveia", "Pão Integral", "Frutas (Banana, Maçã, Mamão)", "Macarrão Integral", "Feijão", "Lentilha"],
    "Gorduras": ["Azeite de Oliva Extra Virgem", "Abacate", "Castanhas (Nozes, Amêndoas)", "Pasta de Amendoim Integral", "Gema de Ovo", "Sementes (Chia, Linhaça)", "Salmão"]
}
# (O resto do seu código, como EXERCICIOS_DB, continua abaixo)
EXERCICIOS_DB = {
    # ==================== PERNAS ====================
    # Foco Quadríceps/Geral
    'Agachamento com Barra': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar', 'Joelhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra apoiada nos ombros/trapézio. Pés afastados na largura dos ombros. Desça flexionando quadril e joelhos, mantendo a coluna neutra e o peito aberto. Suba estendendo quadril e joelhos.'
    },
    'Agachamento Frontal': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar', 'Joelhos', 'Punhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra apoiada na parte frontal dos ombros, cotovelos apontando para frente. Mantém o tronco mais ereto que o agachamento tradicional. Desça mantendo o peito aberto e suba estendendo as pernas.'
    },
    'Agachamento com Halteres': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Segure halteres ao lado do corpo com as palmas voltadas para dentro. Mantenha o tronco ereto, desça flexionando quadril e joelhos. Suba estendendo.'
    },
    'Agachamento Goblet': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Segure um halter verticalmente contra o peito. Pés levemente mais afastados que os ombros. Desça o mais fundo possível, mantendo o tronco ereto e os cotovelos entre os joelhos. Suba.'
    },
    'Agachamento Búlgaro': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Halteres', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Uma perna à frente, a outra com o peito do pé apoiado em um banco atrás. Segure halteres ao lado do corpo ou sem peso. Desça flexionando o joelho da frente até aproximadamente 90°. Suba estendendo.'
    },
    'Afundo (Passada)': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Halteres/Barra', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, dê um passo largo à frente e desça flexionando ambos os joelhos até formar ângulos de 90°. A perna de trás quase toca o chão. Empurre com a perna da frente para voltar à posição inicial. Alterne as pernas.'
    },
    'Afundo Estacionário': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Halteres', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Mantenha uma perna à frente e outra atrás em posição fixa. Desça verticalmente flexionando os joelhos. Suba mantendo a mesma posição dos pés. Complete as repetições e troque de perna.'
    },
    'Leg Press 45°': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sente-se na máquina com as costas bem apoiadas. Pés na plataforma afastados na largura dos ombros. Destrave e desça controladamente flexionando os joelhos (aprox. 90°). Empurre de volta à posição inicial sem travar os joelhos.'
    },
    'Hack Squat': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Posicione-se na máquina com as costas apoiadas e pés na plataforma. Ombros sob os apoios. Destrave e desça flexionando os joelhos profundamente. Empurre para cima até quase estender completamente.'
    },
    'Cadeira Extensora': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sente-se na máquina, ajuste o apoio dos tornozelos. Estenda completamente os joelhos, levantando o peso. Retorne controladamente à posição inicial.'
    },
    'Sissy Squat': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, segure em um apoio para equilíbrio. Incline o tronco para trás enquanto flexiona os joelhos, mantendo quadril, tronco e coxas alinhados. Desça controladamente e volte contraindo os quadríceps.'
    },

    # Foco Posterior (Isquiotibiais)
    'Mesa Flexora': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deite-se de bruços na máquina, joelhos alinhados com o eixo, tornozelos sob o apoio. Flexione os joelhos trazendo os calcanhares em direção aos glúteos. Retorne controladamente.'
    },
    'Mesa Flexora Sentada': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina com as costas apoiadas, tornozelos sobre o apoio. Flexione os joelhos puxando os calcanhares para baixo. Retorne controladamente.'
    },
    'Stiff com Halteres': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres na frente das coxas. Mantenha os joelhos levemente flexionados (quase estendidos). Desça o tronco projetando o quadril para trás, mantendo a coluna reta e os halteres próximos às pernas. Suba contraindo posteriores e glúteos.'
    },
    'Stiff com Barra': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada pronada. Mantenha joelhos levemente flexionados. Desça inclinando o tronco e projetando o quadril para trás, barra próxima às pernas. Suba contraindo posteriores.'
    },
    'Levantamento Terra Romeno': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra/Halteres', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Similar ao stiff, mas inicia com a barra já elevada (não do chão). Desça até a barra atingir aproximadamente a altura dos joelhos/canelas. Foco na fase excêntrica dos posteriores.'
    },
    'Levantamento Terra': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra no chão. Pés sob a barra, na largura do quadril. Agache, segure a barra com pegada pronada. Mantenha coluna neutra, peito aberto. Levante estendendo quadril e joelhos simultaneamente até ficar completamente ereto. Desça controladamente.'
    },
    'Good Morning': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Barra/Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra apoiada nos ombros (como agachamento). Em pé, joelhos levemente flexionados. Incline o tronco para frente projetando o quadril para trás, mantendo coluna reta. Volte contraindo posteriores e lombar.'
    },

    # Glúteos
    'Elevação Pélvica': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Barra', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas com os ombros apoiados em um banco e joelhos flexionados. Apoie uma barra sobre o quadril. Desça o quadril e eleve-o o máximo possível, contraindo os glúteos no topo. Controle a descida.'
    },
    'Hip Thrust Unilateral': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar à elevação pélvica, mas executada com uma perna de cada vez. Outra perna estendida no ar. Aumenta a ativação do glúteo trabalhado.'
    },
    'Extensão de Quadril (Coice)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal/Caneleiras/Polia', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em quatro apoios ou em pé na polia/com caneleiras. Estenda uma perna para trás e para cima, contraindo o glúteo. Mantenha o abdômen contraído e evite arquear a lombar. Retorne controladamente.'
    },
    'Coice na Polia (Cabo)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé de frente para a polia baixa, prenda o tornozelo no cabo. Estenda o quadril levando a perna para trás, contraindo o glúteo. Controle o retorno.'
    },
    'Abdução de Quadril': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina/Elásticos/Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina abdutora, deitado de lado, ou em pé com elásticos/caneleiras. Afaste a(s) perna(s) lateralmente contra a resistência, focando no glúteo lateral (médio/mínimo). Retorne controladamente.'
    },
    'Abdução Deitado de Lado': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal/Caneleiras', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de lado, perna de baixo flexionada para apoio. Eleve a perna de cima lateralmente mantendo-a estendida. Contraia o glúteo médio. Desça controladamente.'
    },
    'Glúteo Sapinho (Frog Pump)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, junte as solas dos pés e afaste os joelhos (posição de "sapo"). Calcanhares próximos aos glúteos. Eleve o quadril do chão, contraindo fortemente os glúteos. Desça controladamente.'
    },
    'Step Up': {
        'grupo': 'Pernas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal/Halteres', 'restricoes': ['Joelhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em frente a um banco ou caixa. Suba colocando um pé completamente sobre o banco, empurre com essa perna (não impulsione com a de trás). Fique em pé sobre o banco. Desça controladamente. Alterne as pernas.'
    },

    # Panturrilhas
    'Panturrilha no Leg Press': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado no Leg Press, ponta dos pés na parte inferior da plataforma, calcanhares para fora. Joelhos estendidos (não travados). Empurre a plataforma apenas com a flexão plantar. Retorne alongando.'
    },
    'Panturrilha em Pé (Máquina)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé na máquina específica, ombros sob os apoios, ponta dos pés na plataforma. Eleve os calcanhares o máximo possível contraindo as panturrilhas. Desça alongando completamente.'
    },
    'Panturrilha Sentado (Máquina)': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina específica, joelhos sob os apoios, ponta dos pés na plataforma. Eleve os calcanhares contraindo as panturrilhas (foco no sóleo). Desça alongando.'
    },
    'Panturrilha com Halteres': {
        'grupo': 'Pernas', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé com halteres nas mãos, ponta dos pés em uma elevação (step ou anilha). Eleve os calcanhares o máximo possível. Desça alongando completamente. Pode ser feito unilateral para maior amplitude.'
    },

    # ==================== PEITO ====================
    'Supino Reto com Barra': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, pés firmes no chão. Pegada na barra um pouco mais larga que os ombros. Desça a barra controladamente até tocar levemente o meio do peito. Empurre a barra de volta para cima.'
    },
    'Supino Reto com Halteres': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, segure os halteres acima do peito com as palmas para frente. Desça os halteres lateralmente, flexionando os cotovelos. Empurre os halteres de volta para cima.'
    },
    'Supino Inclinado com Barra': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado em banco inclinado (30-45°). Pegada similar ao supino reto. Desça a barra em direção à parte superior do peito. Empurre para cima.'
    },
    'Supino Inclinado com Halteres': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado em um banco inclinado (30-45°). Movimento similar ao supino reto com halteres, mas descendo os pesos em direção à parte superior do peito.'
    },
    'Supino Declinado': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Barra/Halteres', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado em banco declinado (cabeça mais baixa que o quadril), pés presos. Desça a barra/halteres em direção à parte inferior do peito. Empurre para cima. Foco no peitoral inferior.'
    },
    'Crucifixo com Halteres': {
        'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado no banco reto, halteres acima do peito, palmas das mãos voltadas uma para a outra, cotovelos levemente flexionados. Abra os braços descendo os halteres lateralmente em um arco. Retorne à posição inicial contraindo o peito.'
    },
    'Crucifixo Inclinado': {
        'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar ao crucifixo reto, mas executado em banco inclinado (30-45°). Maior ênfase no peitoral superior.'
    },
    'Crucifixo na Polia (Cross Over)': {
        'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé entre as polias altas, segure as manoplas. Incline levemente o tronco à frente. Com cotovelos levemente flexionados, puxe as manoplas em um arco para frente, juntando-as na frente do peito. Retorne controladamente.'
    },
    'Peck Deck (Voador)': {
        'grupo': 'Peito', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina, costas apoiadas. Cotovelos nos apoios ou segurando as manoplas. Junte os braços à frente do peito contraindo o peitoral. Retorne controladamente.'
    },
    'Flexão de Braço': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Punhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Mãos no chão afastadas na largura dos ombros (ou um pouco mais). Corpo reto da cabeça aos calcanhares. Desça o peito flexionando os cotovelos. Empurre de volta à posição inicial.'
    },
    'Flexão Declinada': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Punhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Pés elevados em um banco, mãos no chão. Execução similar à flexão tradicional, mas com maior ênfase no peitoral superior devido ao ângulo.'
    },
    'Flexão Inclinada': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Punhos'],
        'niveis_permitidos': ['Iniciante'],
        'descricao': 'Mãos elevadas em um banco ou barra, pés no chão. Versão mais fácil da flexão tradicional, ideal para iniciantes.'
    },
    'Supino na Máquina': {
        'grupo': 'Peito', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina de supino, costas apoiadas. Empurre as manoplas para frente estendendo os cotovelos. Retorne controladamente. Movimento guiado e seguro.'
    },

    # ==================== COSTAS ====================
    'Barra Fixa': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Pendure-se na barra com pegada pronada (palmas para frente) ou supinada (palmas para você), mãos afastadas na largura dos ombros ou mais. Puxe o corpo para cima até o queixo passar a barra, contraindo as costas. Desça controladamente.'
    },
    'Barra Fixa Supinada': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Similar à barra fixa, mas com pegada supinada (palmas voltadas para você). Mãos na largura dos ombros. Maior ativação dos bíceps e parte inferior do latíssimo.'
    },
    'Puxada Alta (Lat Pulldown)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina, ajuste o apoio dos joelhos. Pegada na barra mais larga que os ombros. Puxe a barra verticalmente em direção à parte superior do peito, mantendo o tronco estável e contraindo as costas. Retorne controladamente.'
    },
    'Puxada Frontal com Pegada Fechada': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar à puxada alta, mas com pegada neutra ou supinada fechada (mãos próximas). Maior ativação da parte inferior do latíssimo e bíceps.'
    },
    'Puxada com Triângulo': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Na polia alta, use o acessório em forma de V (triângulo). Pegada neutra. Puxe em direção ao peito, mantendo cotovelos próximos ao corpo.'
    },
    'Remada Curvada com Barra': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Incline o tronco à frente (45-60°), mantendo a coluna reta e os joelhos levemente flexionados. Pegada pronada na barra. Puxe a barra em direção ao abdômen/peito baixo, contraindo as costas. Desça controladamente.'
    },
    'Remada Curvada Supinada': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Similar à remada curvada, mas com pegada supinada (palmas para cima). Maior ativação dos bíceps e parte inferior do latíssimo.'
    },
    'Remada Sentada (máquina)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina com o peito apoiado (se houver). Puxe as manoplas/pegadores em direção ao corpo, mantendo os cotovelos próximos ao tronco e contraindo as escápulas. Retorne controladamente.'
    },
    'Remada na Polia Baixa': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado de frente para a polia baixa, pés apoiados. Puxe a barra/triângulo em direção ao abdômen, mantendo o tronco estável. Contraia as escápulas. Retorne alongando os braços.'
    },
    'Remada Unilateral (Serrote)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Apoie um joelho e a mão do mesmo lado em um banco. Mantenha o tronco paralelo ao chão e a coluna reta. Com o outro braço, puxe o halter em direção ao quadril/costela, mantendo o cotovelo próximo ao corpo. Desça controladamente.'
    },
    'Remada com Halteres (Ambos os Braços)': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Incline o tronco à frente, joelhos levemente flexionados, halteres pendurados. Puxe ambos os halteres simultaneamente em direção ao abdômen/costelas, mantendo cotovelos próximos ao corpo.'
    },
    'Pullover com Halter': {
        'grupo': 'Costas', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado em um banco (perpendicular ou ao longo), segure um halter com ambas as mãos acima do peito. Desça o halter em um arco sobre a cabeça mantendo leve flexão dos cotovelos. Puxe de volta contraindo dorsal e peito.'
    },
    'Pullover na Polia': {
        'grupo': 'Costas', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé de frente para a polia alta, segure a barra com os braços estendidos acima da cabeça. Puxe a barra em um arco até a frente das coxas, mantendo os braços quase estendidos. Retorne controladamente.'
    },
    'Remada Cavalinho': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Coloque uma barra em um canto ou use máquina específica. Posicione-se sobre a barra, inclinado. Puxe a extremidade da barra em direção ao peito. Movimento similar à remada, mas com pegada única.'
    },
    'Levantamento Terra': {
        'grupo': 'Costas', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Barra no chão. Pés sob a barra, na largura do quadril. Agache, segure a barra com pegada pronada. Mantenha coluna neutra, peito aberto. Levante estendendo quadril e joelhos simultaneamente. Trabalha toda a cadeia posterior.'
    },

    # ==================== OMBROS ====================
    'Desenvolvimento Militar com Barra': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Lombar', 'Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), barra apoiada na parte superior do peito, pegada pronada um pouco mais larga que os ombros. Empurre a barra verticalmente para cima até estender os cotovelos. Desça controladamente até a posição inicial.'
    },
    'Desenvolvimento com Halteres (sentado)': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado em um banco com encosto, segure os halteres na altura dos ombros com as palmas para frente. Empurre os halteres verticalmente para cima. Desça controladamente.'
    },
    'Desenvolvimento com Halteres (em pé)': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, halteres na altura dos ombros. Empurre os halteres para cima. Exige maior estabilização do core comparado à versão sentada.'
    },
    'Desenvolvimento Arnold': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Sentado, inicie com halteres na frente dos ombros, palmas voltadas para você. Ao empurrar para cima, rode os punhos para que as palmas fiquem para frente no topo. Inverta o movimento na descida.'
    },
    'Desenvolvimento na Máquina': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado na máquina, ajuste a altura do banco. Empurre as manoplas para cima. Movimento guiado e seguro, ideal para iniciantes.'
    },
    'Elevação Lateral': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres ao lado do corpo. Mantenha os cotovelos levemente flexionados. Eleve os braços lateralmente até a altura dos ombros. Desça controladamente.'
    },
    'Elevação Lateral na Polia': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé de lado para a polia baixa, segure a manopla do lado oposto ao da polia. Eleve o braço lateralmente mantendo tensão constante. Desça controladamente.'
    },
    'Elevação Lateral Inclinado': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Incline o tronco lateralmente apoiando uma mão em um suporte. Com o braço livre, execute elevação lateral. Isola melhor o deltoide lateral removendo a ajuda do trapézio.'
    },
    'Elevação Frontal': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres na frente das coxas (pegada pronada ou neutra). Eleve um braço de cada vez (ou ambos) para frente, mantendo o cotovelo levemente flexionado, até a altura dos ombros. Desça controladamente.'
    },
    'Elevação Frontal com Barra': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada pronada na frente das coxas. Eleve a barra para frente até a altura dos ombros, mantendo os braços quase estendidos. Desça controladamente.'
    },
    'Remada Alta': {
        'grupo': 'Ombros', 'tipo': 'Composto', 'equipamento': 'Barra/Halteres', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada pronada fechada (mãos próximas). Puxe a barra verticalmente ao longo do corpo até a altura do queixo, cotovelos apontando para cima e para fora. Desça controladamente.'
    },
    'Crucifixo Inverso com Halteres': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Incline o tronco para frente (sentado ou em pé curvado), halteres pendurados. Eleve os braços lateralmente em arco, cotovelos levemente flexionados, até a altura dos ombros. Foco no deltoide posterior.'
    },
    'Crucifixo Inverso na Máquina (Peck Deck Inverso)': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado de frente para a máquina peck deck (posição inversa), segure as manoplas. Abra os braços puxando para trás, focando no deltoide posterior. Retorne controladamente.'
    },
    'Face Pull': {
        'grupo': 'Ombros', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Na polia alta com corda, segure as pontas da corda. Puxe em direção ao rosto, abrindo os cotovelos para fora. Foco no deltoide posterior e trapézio médio. Excelente para saúde dos ombros.'
    },

    # ==================== BÍCEPS ====================
    'Rosca Direta com Barra': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': ['Punhos'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada supinada (palmas para cima), mãos na largura dos ombros. Mantenha os cotovelos fixos ao lado do corpo. Flexione os cotovelos trazendo a barra em direção aos ombros. Desça controladamente.'
    },
    'Rosca Direta com Barra W': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar à rosca com barra reta, mas usando barra W (zigzag). A pegada angulada reduz o estresse nos punhos e antebraços.'
    },
    'Rosca Direta com Halteres': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), segure halteres ao lado do corpo com pegada supinada. Mantenha os cotovelos fixos. Flexione os cotovelos, elevando os halteres. Pode ser feito simultaneamente ou alternadamente. Desça controladamente.'
    },
    'Rosca Alternada': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé ou sentado, execute a rosca direta alternando os braços. Permite maior foco em cada braço individualmente e possibilita usar cargas ligeiramente maiores.'
    },
    'Rosca Martelo': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé (ou sentado), segure halteres ao lado do corpo com pegada neutra (palmas voltadas para o corpo). Mantenha os cotovelos fixos. Flexione os cotovelos, elevando os halteres. Desça controladamente.'
    },
    'Rosca Concentrada': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado, apoie a parte de trás do braço na parte interna da coxa. Segure um halter com pegada supinada. Flexione o cotovelo elevando o halter. Maior isolamento do bíceps.'
    },
    'Rosca Scott (Banco Scott)': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado no banco Scott, braços apoiados na almofada inclinada. Segure a barra com pegada supinada. Flexione os cotovelos. O apoio impede o balanço e isola melhor o bíceps.'
    },
    'Rosca na Polia Baixa': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé de frente para a polia baixa, segure a barra. Execute a rosca mantendo tensão constante durante todo o movimento. Permite bom trabalho na fase excêntrica.'
    },
    'Rosca 21': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Método de treinamento: 7 repetições da metade inferior (até 90°), 7 repetições da metade superior (de 90° até completo), 7 repetições completas. Total de 21 repetições contínuas. Alta intensidade.'
    },
    'Rosca Inversa': {
        'grupo': 'Bíceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar à rosca direta, mas com pegada pronada (palmas para baixo). Trabalha mais intensamente os antebraços e braquiorradial, além do bíceps.'
    },

    # ==================== TRÍCEPS ====================
    'Tríceps Testa': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': ['Cotovelos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado em um banco reto, segure uma barra W (ou halteres com pegada neutra) acima do peito com os braços estendidos. Mantenha os braços (úmeros) parados. Flexione os cotovelos descendo o peso em direção à testa/cabeça. Estenda os cotovelos de volta à posição inicial.'
    },
    'Tríceps Francês (Testa com Halteres)': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': ['Cotovelos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado, segure halteres com pegada neutra (palmas frente a frente). Mantenha os cotovelos apontando para cima. Desça os halteres ao lado da cabeça flexionando apenas os cotovelos. Estenda.'
    },
    'Tríceps Pulley': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, de frente para a polia alta, segure a barra ou corda com pegada pronada (ou neutra na corda). Mantenha os cotovelos fixos ao lado do corpo. Estenda completamente os cotovelos empurrando a barra/corda para baixo. Retorne controladamente.'
    },
    'Tríceps Pulley com Corda': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar ao tríceps pulley, mas usando corda. Na parte final do movimento, separe as pontas da corda para os lados aumentando a contração do tríceps.'
    },
    'Tríceps Unilateral na Polia': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Um braço por vez na polia alta. Permite maior amplitude de movimento e foco em cada braço. Boa correção de assimetrias.'
    },
    'Tríceps Coice': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Incline o tronco para frente, joelho e mão de um lado apoiados em banco. Cotovelo do braço trabalhado fixo junto ao corpo, antebraço perpendicular ao chão. Estenda o cotovelo levando o halter para trás. Retorne controladamente.'
    },
    'Tríceps Overhead (Francês em Pé)': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Halteres/Barra', 'restricoes': ['Ombros', 'Cotovelos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé ou sentado, segure um halter (ou barra) acima da cabeça com ambas as mãos. Mantenha os cotovelos apontando para cima. Desça o peso atrás da cabeça flexionando apenas os cotovelos. Estenda de volta.'
    },
    'Tríceps na Polia Alta (Overhead)': {
        'grupo': 'Tríceps', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'De costas para a polia alta, segure a corda acima da cabeça. Cotovelos apontando para frente. Estenda os cotovelos empurrando a corda para frente e para cima. Ênfase na cabeça longa do tríceps.'
    },
    'Mergulho no Banco': {
        'grupo': 'Tríceps', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Ombros', 'Punhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Apoie as mãos em um banco atrás do corpo, dedos para frente. Mantenha as pernas estendidas à frente (ou joelhos flexionados para facilitar). Flexione os cotovelos descendo o corpo verticalmente. Empurre de volta para cima estendendo os cotovelos.'
    },
    'Mergulho nas Paralelas': {
        'grupo': 'Tríceps', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': ['Ombros'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Apoie-se nas barras paralelas com os braços estendidos. Mantenha o corpo mais vertical para foco no tríceps (inclinado trabalha mais peito). Desça flexionando os cotovelos. Empurre para cima.'
    },
    'Supino Fechado': {
        'grupo': 'Tríceps', 'tipo': 'Composto', 'equipamento': 'Barra', 'restricoes': ['Punhos'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado no banco, pegada na barra mais fechada que os ombros. Desça a barra em direção ao peito mantendo cotovelos próximos ao corpo. Empurre para cima. Trabalha tríceps e peito.'
    },

    # ==================== CORE ====================
    'Prancha': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Apoie os antebraços e as pontas dos pés no chão. Mantenha o corpo reto da cabeça aos calcanhares, contraindo o abdômen e os glúteos. Evite elevar ou baixar demais o quadril. Sustente a posição.'
    },
    'Prancha Lateral': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de lado, apoie o antebraço e a lateral do pé. Eleve o quadril formando uma linha reta. Mantenha a posição contraindo o core e os oblíquos. Trabalha principalmente os músculos laterais do abdômen.'
    },
    'Prancha com Elevação de Perna': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Na posição de prancha, eleve alternadamente cada perna mantendo o quadril estável. Aumenta o desafio de estabilização.'
    },
    'Abdominal Crunch': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, joelhos flexionados e pés no chão (ou pernas elevadas). Mãos atrás da cabeça (sem puxar) ou cruzadas no peito. Eleve a cabeça e os ombros do chão, contraindo o abdômen ("enrolando" a coluna). Retorne controladamente.'
    },
    'Abdominal na Polia': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Ajoelhado de frente para a polia alta, segure a corda atrás da cabeça. Flexione o tronco para baixo contraindo o abdômen. Retorne controladamente. Permite progressão com carga.'
    },
    'Abdominal Bicicleta': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, mãos atrás da cabeça, pernas elevadas. Leve o cotovelo em direção ao joelho oposto enquanto estende a outra perna. Alterne em movimento de pedalada. Trabalha reto abdominal e oblíquos.'
    },
    'Abdominal Infra (Reverso)': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, pernas flexionadas ou estendidas. Eleve o quadril do chão trazendo os joelhos em direção ao peito. Foco no abdômen inferior. Desça controladamente.'
    },
    'Elevação de Pernas': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, pernas estendidas. Pode colocar as mãos sob a lombar para apoio. Mantendo as pernas retas (ou levemente flexionadas), eleve-as até formarem 90° com o tronco. Desça controladamente quase até o chão, sem deixar a lombar arquear.'
    },
    'Elevação de Pernas Suspenso': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Pendurado em uma barra fixa, eleve as pernas estendidas (ou joelhos flexionados para facilitar) até formarem 90° com o tronco. Desça controladamente. Versão avançada e muito eficaz.'
    },
    'Russian Twist': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado com o tronco inclinado para trás, joelhos flexionados, pés elevados do chão. Segure um halter ou medicine ball. Rotacione o tronco alternando os lados, tocando o peso no chão ao lado do corpo. Trabalha oblíquos.'
    },
    'Prancha Dinâmica (Mountain Climber)': {
        'grupo': 'Core', 'tipo': 'Composto', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Na posição de prancha alta (braços estendidos), traga alternadamente os joelhos em direção ao peito em movimento de corrida. Mantém o core ativado e adiciona componente cardiovascular.'
    },
    'Prancha com Toque no Ombro': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Na posição de prancha alta, alterne tocando o ombro oposto com cada mão. Mantém o quadril estável durante o movimento. Excelente para estabilização e anti-rotação.'
    },
    'Dead Bug': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de costas, braços estendidos para cima, joelhos flexionados a 90°. Desça simultaneamente um braço sobre a cabeça e a perna oposta estendida, mantendo a lombar colada no chão. Retorne e alterne. Excelente para coordenação e estabilidade.'
    },
    'Superman': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Deitado de bruços, braços e pernas estendidos. Eleve simultaneamente braços, peito e pernas do chão, contraindo lombar e glúteos. Mantenha por um instante e retorne controladamente.'
    },
    'Bird Dog': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em quatro apoios. Estenda simultaneamente um braço para frente e a perna oposta para trás, formando uma linha reta. Mantenha o core estável. Retorne e alterne. Trabalha estabilização e equilíbrio.'
    },
    'Pallof Press': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Em pé de lado para a polia média, segure a manopla próxima ao peito. Estenda os braços para frente resistindo à rotação do tronco. Mantenha e retorne. Excelente exercício anti-rotação.'
    },
    'Abdominal Canivete (V-Up)': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado completamente estendido. Simultaneamente eleve pernas e tronco tentando tocar as mãos nos pés, formando um "V". Desça controladamente. Exercício avançado e intenso.'
    },
    'Roda Abdominal (Ab Wheel)': {
        'grupo': 'Core', 'tipo': 'Composto', 'equipamento': 'Acessório', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Ajoelhado, segure a roda abdominal. Role para frente estendendo o corpo o máximo possível mantendo o core contraído. Puxe de volta contraindo o abdômen. Exercício muito desafiador.'
    },
    'Hollow Body Hold': {
        'grupo': 'Core', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado de costas, eleve ligeiramente os ombros e pernas do chão (pernas estendidas), braços ao lado do corpo ou estendidos acima da cabeça. Lombar colada no chão. Mantenha a posição. Base do core em ginástica.'
    },
    'Windshield Wiper': {
        'grupo': 'Core', 'tipo': 'Isolado', 'equipamento': 'Peso Corporal', 'restricoes': ['Lombar'],
        'niveis_permitidos': ['Intermediário/Avançado'],
        'descricao': 'Deitado de costas com pernas elevadas a 90°, braços abertos para os lados. Desça as pernas juntas para um lado (sem tocar o chão) e retorne ao centro. Alterne. Trabalha intensamente os oblíquos.'
    },

    # ==================== TRAPÉZIO ====================
    'Encolhimento com Barra': {
        'grupo': 'Trapézio', 'tipo': 'Isolado', 'equipamento': 'Barra', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada pronada, braços estendidos na frente do corpo. Eleve os ombros em direção às orelhas contraindo o trapézio. Desça controladamente. Não flexione os cotovelos.'
    },
    'Encolhimento com Halteres': {
        'grupo': 'Trapézio', 'tipo': 'Isolado', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure halteres ao lado do corpo, braços estendidos. Eleve os ombros em direção às orelhas. Desça controladamente. Permite maior amplitude de movimento que a barra.'
    },
    'Encolhimento na Máquina': {
        'grupo': 'Trapézio', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Utilize máquina específica para encolhimento (trap bar ou smith machine). Execute o movimento vertical elevando os ombros. Trajetória estável e controlada.'
    },
    'Face Pull': {
        'grupo': 'Trapézio', 'tipo': 'Isolado', 'equipamento': 'Máquina', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Na polia alta com corda, segure as pontas da corda. Puxe em direção ao rosto, abrindo os cotovelos para fora. Trabalha trapézio médio/inferior, deltoide posterior e manguito rotador.'
    },

    # ==================== ANTEBRAÇO ====================
    'Rosca Punho (Wrist Curl)': {
        'grupo': 'Antebraço', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Sentado, antebraços apoiados nas coxas ou em um banco, punhos para fora da borda. Segure a barra/halteres com pegada supinada. Flexione os punhos para cima. Trabalha flexores do antebraço.'
    },
    'Rosca Punho Inversa': {
        'grupo': 'Antebraço', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Similar à rosca de punho, mas com pegada pronada (palmas para baixo). Estenda os punhos para cima. Trabalha extensores do antebraço.'
    },
    'Farmer Walk (Caminhada do Fazendeiro)': {
        'grupo': 'Antebraço', 'tipo': 'Composto', 'equipamento': 'Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Segure halteres pesados ao lado do corpo. Caminhe mantendo postura ereta e ombros para trás. Trabalha intensamente a pegada, antebraços, trapézio e core. Excelente para força funcional.'
    },
    'Dead Hang (Suspensão na Barra)': {
        'grupo': 'Antebraço', 'tipo': 'Isométrico', 'equipamento': 'Peso Corporal', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Pendure-se em uma barra com pegada pronada, braços estendidos. Mantenha a suspensão o máximo de tempo possível. Desenvolve força de pegada e alonga os ombros.'
    },
    'Rosca Inversa': {
        'grupo': 'Antebraço', 'tipo': 'Isolado', 'equipamento': 'Barra/Halteres', 'restricoes': [],
        'niveis_permitidos': ['Iniciante', 'Intermediário/Avançado'],
        'descricao': 'Em pé, segure a barra com pegada pronada. Execute uma rosca direta mantendo as palmas para baixo. Trabalha intensamente braquiorradial e extensores do antebraço.'
    },
}

PREMADE_WORKOUTS_DB = {
    # Treino 1
    "ppl_6d_adv": {
        "title": "Push/Pull/Legs (PPL) 6 Dias",
        "description": "Divisão clássica PPL 2x/semana. Foco em hipertrofia e força para avançados.",
        "image_url": "https://images.pexels.com/photos/1552242/pexels-photo-1552242.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Push A (Peito/Ombro/Tríceps)": [
                {"Exercício": "Supino Reto com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Testa", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 2: Pull A (Costas/Bíceps)": [
                {"Exercício": "Barra Fixa", "Séries": "4", "Repetições": "Falha", "Descanso": "90s"},
                {"Exercício": "Remada Curvada com Barra", "Séries": "3", "Repetições": "6-10", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Barra", "Séries": "3", "Repetições": "8-12", "Descanso": "45s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 3: Legs A (Pernas)": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "120s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Afundo Estacionário", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "4", "Repetições": "10-15", "Descanso": "30s"},
            ],
            "Dia 4: Push B (Variação)": [
                {"Exercício": "Supino Reto com Halteres", "Séries": "4", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Desenvolvimento Militar com Barra", "Séries": "3", "Repetições": "6-10", "Descanso": "60s"},
                {"Exercício": "Mergulho nas Paralelas", "Séries": "3", "Repetições": "Falha", "Descanso": "60s"},
                {"Exercício": "Elevação Frontal", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley com Corda", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 5: Pull B (Variação)": [
                {"Exercício": "Levantamento Terra", "Séries": "3", "Repetições": "5", "Descanso": "120s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Face Pull", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Rosca Scott (Banco Scott)", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
            ],
            "Dia 6: Legs B (Variação)": [
                {"Exercício": "Agachamento Frontal", "Séries": "4", "Repetições": "8-12", "Descanso": "120s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Agachamento Búlgaro", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Panturrilha Sentado (Máquina)", "Séries": "4", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 2
    "ul_4d_hipertrofia": {
        "title": "Upper/Lower (Hipertrofia)",
        "description": "Divisão de 4 dias (Superior/Inferior 2x) para frequência 2x/semana.",
        "image_url": "https://images.pexels.com/photos/1954524/pexels-photo-1954524.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Upper A (Foco Força)": [
                {"Exercício": "Supino Reto com Barra", "Séries": "3", "Repetições": "6-8", "Descanso": "90s"},
                {"Exercício": "Remada Curvada com Barra", "Séries": "3", "Repetições": "6-8", "Descanso": "90s"},
                {"Exercício": "Desenvolvimento Militar com Barra", "Séries": "3", "Repetições": "8-10", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Barra", "Séries": "3", "Repetições": "8-10", "Descanso": "45s"},
                {"Exercício": "Tríceps Testa", "Séries": "3", "Repetições": "8-10", "Descanso": "45s"},
            ],
            "Dia 2: Lower A (Foco Força)": [
                {"Exercício": "Agachamento com Barra", "Séries": "3", "Repetições": "6-8", "Descanso": "120s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "8-10", "Descanso": "60s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "30s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "60s", "Descanso": "30s"},
            ],
            "Dia 3: Upper B (Foco Volume)": [
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "4", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley com Corda", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 4: Lower B (Foco Volume)": [
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "12-15", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Agachamento Búlgaro", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Panturrilha Sentado (Máquina)", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 3
    "fullbody_3d_iniciante": {
        "title": "Full Body 3 Dias (Iniciante)",
        "description": "Treino de corpo inteiro 3x/semana, ideal para quem está começando.",
        "image_url": "https://images.pexels.com/photos/3289711/pexels-photo-3289711.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Full Body A": [
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "30-60s", "Descanso": "30s"},
            ],
            "Dia 2: Full Body B": [
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Supino na Máquina", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Tríceps Pulley", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Abdominal Crunch", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ],
            "Dia 3: Full Body C": [
                {"Exercício": "Afundo Estacionário", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "10-12/lado", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Elevação de Pernas", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 4
    "foco_gluteo_4d": {
        "title": "Foco em Glúteos (4 Dias)",
        "description": "Divisão Upper/Lower com ênfase extra em glúteos e posteriores.",
        "image_url": "https://images.pexels.com/photos/6550853/pexels-photo-6550853.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Inferiores (Foco Glúteo/Post)": [
                {"Exercício": "Elevação Pélvica", "Séries": "4", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Agachamento Búlgaro", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 2: Superiores (Geral)": [
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "10-12/lado", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Tríceps Pulley", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 3: Inferiores (Foco Quad/Glúteo)": [
                {"Exercício": "Agachamento Goblet", "Séries": "4", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Cadeira Extensora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Abdução de Quadril", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
            ],
            "Dia 4: Superiores & Core": [
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Flexão de Braço", "Séries": "3", "Repetições": "Falha", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "60s", "Descanso": "30s"},
                {"Exercício": "Abdominal Infra (Reverso)", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 5
    "ppl_ul_5d_interm": {
        "title": "Intermediário 5 Dias (PPL + UL)",
        "description": "Divisão PPL clássica (Foco Força) + 2 dias Upper/Lower (Foco Volume).",
        "image_url": "https://images.pexels.com/photos/1552252/pexels-photo-1552252.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Push (Peito/Ombro/Tríceps)": [
                {"Exercício": "Supino Reto com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Tríceps Testa", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 2: Pull (Costas/Bíceps)": [
                {"Exercício": "Barra Fixa", "Séries": "4", "Repetições": "Falha", "Descanso": "90s"},
                {"Exercício": "Remada Curvada com Barra", "Séries": "3", "Repetições": "6-10", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Barra", "Séries": "3", "Repetições": "8-12", "Descanso": "45s"},
                {"Exercício": "Face Pull", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
            ],
            "Dia 3: Legs (Pernas/Core)": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "120s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "30s"},
                {"Exercício": "Elevação de Pernas Suspenso", "Séries": "3", "Repetições": "Falha", "Descanso": "60s"},
            ],
            "Dia 4: Upper (Volume)": [
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "12-15", "Descanso": "60s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley com Corda", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 5: Lower (Volume/Core)": [
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "15-20", "Descanso": "60s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "12-15", "Descanso": "60s"},
                {"Exercício": "Agachamento Búlgaro", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "Falha (max 90s)", "Descanso": "45s"},
            ]
        }
    },
    # Treino 6
    "ab_4d_iniciante_split": {
        "title": "Iniciante 4 Dias (Split A/B)",
        "description": "Treino A/B alternado (A: Push/Core, B: Pull/Legs) para focar na base.",
        "image_url": "https://images.pexels.com/photos/2204196/pexels-photo-2204196.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Treino A: Peito/Ombro/Tríceps + Core": [
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Tríceps Pulley", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Abdominal Crunch", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ],
            "Treino B: Costas/Bíceps + Pernas": [
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "10-12", "Descanso": "90s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Rosca Direta com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ]
        }
    },
    # Treino 7
    "ul_4d_forca": {
        "title": "Força Upper/Lower (4 Dias)",
        "description": "Treino focado em progressão de carga nos exercícios compostos. Ideal para quem quer ficar mais forte.",
        "image_url": "https://images.pexels.com/photos/116077/pexels-photo-116077.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Upper Força (Peito/Costas)": [
                {"Exercício": "Supino Reto com Barra", "Séries": "4", "Repetições": "4-6", "Descanso": "120s"},
                {"Exercício": "Remada Curvada com Barra", "Séries": "4", "Repetições": "4-6", "Descanso": "120s"},
                {"Exercício": "Desenvolvimento Militar com Barra", "Séries": "3", "Repetições": "5-8", "Descanso": "90s"},
                {"Exercício": "Barra Fixa Supinada", "Séries": "3", "Repetições": "Falha", "Descanso": "60s"},
            ],
            "Dia 2: Lower Força (Pernas)": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "4-6", "Descanso": "120s-180s"},
                {"Exercício": "Levantamento Terra Romeno", "Séries": "3", "Repetições": "6-8", "Descanso": "90s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "8-10", "Descanso": "60s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "4", "Repetições": "8-10", "Descanso": "45s"},
            ],
            "Dia 3: Upper Hipertrofia (Variação)": [
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "8-12/lado", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "4", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Rosca Scott (Banco Scott)", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
                {"Exercício": "Tríceps Testa", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
            ],
            "Dia 4: Lower Hipertrofia (Variação)": [
                {"Exercício": "Agachamento Búlgaro", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Cadeira Extensora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Panturrilha Sentado (Máquina)", "Séries": "4", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 8
    "split_5d_peito_bracos": {
        "title": "Avançado 5 Dias (Foco Peito/Braços)",
        "description": "Divisão clássica 'Bro Split' com ênfase no desenvolvimento do peitoral e braços.",
        "image_url": "https://images.pexels.com/photos/2247179/pexels-photo-2247179.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Peito": [
                {"Exercício": "Supino Reto com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Crucifixo na Polia (Cross Over)", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Flexão de Braço", "Séries": "2", "Repetições": "Falha", "Descanso": "60s"},
            ],
            "Dia 2: Costas": [
                {"Exercício": "Levantamento Terra", "Séries": "3", "Repetições": "5-8", "Descanso": "120s"},
                {"Exercício": "Barra Fixa", "Séries": "3", "Repetições": "Falha", "Descanso": "90s"},
                {"Exercício": "Remada Cavalinho", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Puxada com Triângulo", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 3: Pernas": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "8-12", "Descanso": "120s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Stiff com Barra", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "4", "Repetições": "10-15", "Descanso": "30s"},
            ],
            "Dia 4: Ombros/Trapézio": [
                {"Exercício": "Desenvolvimento Militar com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Elevação Lateral", "Séries": "4", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Crucifixo Inverso com Halteres", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Encolhimento com Halteres", "Séries": "4", "Repetições": "10-12", "Descanso": "45s"},
                {"Exercício": "Face Pull", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
            ],
            "Dia 5: Braços (Bíceps/Tríceps)": [
                {"Exercício": "Supino Fechado", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Barra", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Tríceps Testa", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Rosca Scott (Banco Scott)", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley com Corda", "Séries": "3", "Repetições": "12-15", "Descanso": "30s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "12-15", "Descanso": "30s"},
            ]
        }
    },
    # Treino 9
    "ppl_3d_interm": {
        "title": "Push/Pull/Legs (3 Dias)",
        "description": "A divisão PPL clássica. Frequência 1x/semana por grupo, ideal para quem tem 3 dias fixos.",
        "image_url": "https://images.pexels.com/photos/1552249/pexels-photo-1552249.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Push (Peito/Ombro/Tríceps)": [
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 2: Pull (Costas/Bíceps)": [
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "10-12/lado", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
            ],
            "Dia 3: Legs (Pernas/Core)": [
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "4", "Repetições": "10-15", "Descanso": "30s"},
                {"Exercício": "Abdominal Infra (Reverso)", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 10
    "casa_3d_iniciante": {
        "title": "Treino em Casa (Iniciante)",
        "description": "Treino Full Body 3x/semana usando apenas Peso Corporal e Halteres.",
        "image_url": "https://images.pexels.com/photos/4162451/pexels-photo-4162451.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Full Body A": [
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Flexão de Braço", "Séries": "3", "Repetições": "Falha (min 5)", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "10-12/lado", "Descanso": "60s"},
                {"Exercício": "Elevação Pélvica", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "45-60s", "Descanso": "30s"},
            ],
            "Dia 2: Full Body B": [
                {"Exercício": "Afundo Estacionário", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "12-15", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Abdominal Bicicleta", "Séries": "3", "Repetições": "20-30 (total)", "Descanso": "30s"},
            ],
            "Dia 3: Full Body C": [
                {"Exercício": "Agachamento com Halteres", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Flexão Inclinada", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Remada com Halteres (Ambos os Braços)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Mergulho no Banco", "Séries": "3", "Repetições": "Falha (min 8)", "Descanso": "45s"},
                {"Exercício": "Elevação de Pernas", "Séries": "3", "Repetições": "15-20", "Descanso": "30s"},
            ]
        }
    },
    # Treino 11
    "rapido_3d_composto": {
        "title": "Treino Rápido (Foco Compostos)",
        "description": "Treino Full Body 3x/semana focado apenas nos exercícios compostos. Rápido e eficaz.",
        "image_url": "https://images.pexels.com/photos/3837464/pexels-photo-3837464.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Foco A": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Supino Reto com Halteres", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "Falha", "Descanso": "45s"},
            ],
            "Dia 2: Foco B": [
                {"Exercício": "Leg Press 45°", "Séries": "4", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Elevação de Pernas", "Séries": "3", "Repetições": "Falha", "Descanso": "45s"},
            ],
            "Dia 3: Foco C": [
                {"Exercício": "Stiff com Halteres", "Séries": "4", "Repetições": "8-12", "Descanso": "90s"},
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "4", "Repetições": "8-12/lado", "Descanso": "60s"},
                {"Exercício": "Abdominal na Polia", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
            ]
        }
    },
    # Treino 12
    "split_5d_bodybuilding": {
        "title": "Avançado 5 Dias (Bodybuilding)",
        "description": "Divisão clássica de bodybuilding (um grupo por dia) para máximo volume e hipertrofia.",
        "image_url": "https://images.pexels.com/photos/2261482/pexels-photo-2261482.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Peito": [
                {"Exercício": "Supino Inclinado com Halteres", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Crucifixo na Polia (Cross Over)", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Peck Deck (Voador)", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 2: Costas": [
                {"Exercício": "Remada Curvada com Barra", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Pullover na Polia", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 3: Pernas": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "8-12", "Descanso": "120s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Cadeira Extensora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Panturrilha em Pé (Máquina)", "Séries": "4", "Repetições": "10-15", "Descanso": "30s"},
            ],
            "Dia 4: Ombros": [
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "4", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Elevação Lateral na Polia", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Crucifixo Inverso com Halteres", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Encolhimento com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "45s"},
            ],
            "Dia 5: Braços (Bíceps/Tríceps)": [
                {"Exercício": "Rosca Direta com Barra W", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Tríceps Testa", "Séries": "4", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Rosca Alternada", "Séries": "3", "Repetições": "10-12/lado", "Descanso": "45s"},
                {"Exercício": "Tríceps Pulley com Corda", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Rosca Martelo", "Séries": "3", "Repetições": "10-15", "Descanso": "45s"},
                {"Exercício": "Tríceps Unilateral na Polia", "Séries": "3", "Repetições": "10-15/lado", "Descanso": "45s"},
            ]
        }
    },
    # Treino 13
    "fullbody_2d_iniciante": {
        "title": "Iniciante 2 Dias (Full Body)",
        "description": "Treino de corpo inteiro 2x/semana. A melhor opção para quem tem tempo limitado.",
        "image_url": "https://images.pexels.com/photos/1547248/pexels-photo-1547248.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Full Body A": [
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Supino na Máquina", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Tríceps Pulley", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
                {"Exercício": "Prancha", "Séries": "3", "Repetições": "Falha (max 60s)", "Descanso": "30s"},
            ],
            "Dia 2: Full Body B": [
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "12-15", "Descanso": "60s"},
                {"Exercício": "Desenvolvimento na Máquina", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Rosca Direta com Halteres", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ]
        }
    },
    # Treino 14
    "fullbody_3d_forca_adv": {
        "title": "Full Body 3 Dias (Força)",
        "description": "Foco em progressão de carga nos 3 grandes exercícios compostos. Para avançados.",
        "image_url": "https://images.pexels.com/photos/791763/pexels-photo-791763.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1 (Foco Agachamento)": [
                {"Exercício": "Agachamento com Barra", "Séries": "4", "Repetições": "4-6", "Descanso": "120s"},
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "8-12/lado", "Descanso": "60s"},
                {"Exercício": "Rosca Martelo", "Séries": "2", "Repetições": "10-15", "Descanso": "45s"},
            ],
            "Dia 2 (Foco Supino)": [
                {"Exercício": "Supino Reto com Barra", "Séries": "4", "Repetições": "4-6", "Descanso": "120s"},
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "10-15", "Descanso": "60s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "8-12", "Descanso": "60s"},
                {"Exercício": "Elevação Lateral", "Séries": "3", "Repetições": "12-15", "Descanso": "45s"},
            ],
            "Dia 3 (Foco Terra)": [
                {"Exercício": "Levantamento Terra", "Séries": "3", "Repetições": "4-6", "Descanso": "120s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "6-10", "Descanso": "90s"},
                {"Exercício": "Stiff com Halteres", "Séries": "3", "Repetições": "10-12", "Descanso": "60s"},
                {"Exercício": "Tríceps Pulley", "Séries": "2", "Repetições": "10-15", "Descanso": "45s"},
            ]
        }
    },
    # Treino 15
    "metabolico_3d_geral": {
        "title": "Treino Metabólico (Condicionamento)",
        "description": "Foco em condicionamento e queima calórica. Séries mais altas e descansos mais curtos.",
        "image_url": "https://images.pexels.com/photos/6456303/pexels-photo-6456303.jpeg?auto=compress&cs=tinysrgb&w=600",
        "plano": {
            "Dia 1: Full Body A": [
                {"Exercício": "Agachamento Goblet", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Flexão de Braço", "Séries": "3", "Repetições": "Falha", "Descanso": "45s"},
                {"Exercício": "Remada Sentada (máquina)", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Afundo (Passada)", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "45s"},
                {"Exercício": "Prancha Dinâmica (Mountain Climber)", "Séries": "3", "Repetições": "45s", "Descanso": "30s"},
            ],
            "Dia 2: Full Body B": [
                {"Exercício": "Leg Press 45°", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Puxada Alta (Lat Pulldown)", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Desenvolvimento com Halteres (sentado)", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Mesa Flexora", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Abdominal Bicicleta", "Séries": "3", "Repetições": "45s", "Descanso": "30s"},
            ],
            "Dia 3: Full Body C": [
                {"Exercício": "Elevação Pélvica", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Supino Reto com Halteres", "Séries": "3", "Repetições": "15-20", "Descanso": "45s"},
                {"Exercício": "Remada Unilateral (Serrote)", "Séries": "3", "Repetições": "12-15/lado", "Descanso": "45s"},
                {"Exercício": "Step Up", "Séries": "3", "Repetições": "10-12/perna", "Descanso": "45s"},
                {"Exercício": "Russian Twist", "Séries": "3", "Repetições": "45s", "Descanso": "30s"},
            ]
        }
    }
} # <-- FIM DO DICIONÁRIO


EXERCISE_SUBSTITUTIONS = {
    # Substituições PRINCIPALMENTE por RESTRIÇÃO
    'Agachamento com Barra': 'Leg Press 45°',
    'Agachamento Frontal': 'Hack Squat',
    'Stiff com Halteres': 'Mesa Flexora',
    'Stiff com Barra': 'Mesa Flexora',
    'Levantamento Terra Romeno': 'Mesa Flexora',
    'Levantamento Terra': 'Leg Press 45°',
    'Good Morning': 'Mesa Flexora',
    'Remada Curvada com Barra': 'Remada Sentada (máquina)',
    'Remada Curvada Supinada': 'Remada na Polia Baixa',
    'Remada com Halteres (Ambos os Braços)': 'Remada Sentada (máquina)',
    'Remada Cavalinho': 'Remada Sentada (máquina)',
    'Desenvolvimento Militar com Barra': 'Desenvolvimento com Halteres (sentado)',
    'Desenvolvimento com Halteres (em pé)': 'Desenvolvimento com Halteres (sentado)',
    'Remada Alta': 'Elevação Lateral',
    'Supino Reto com Barra': 'Supino Reto com Halteres',
    'Supino Inclinado com Barra': 'Supino Inclinado com Halteres',
    'Supino Declinado': 'Supino Reto com Halteres',
    'Pullover com Halter': 'Pullover na Polia',
    'Tríceps Testa': 'Tríceps Pulley',
    'Tríceps Francês (Testa com Halteres)': 'Tríceps Pulley',
    'Tríceps Overhead (Francês em Pé)': 'Tríceps Pulley',
    'Supino Fechado': 'Tríceps Pulley',
    'Rosca Direta com Barra': 'Rosca Direta com Halteres',
    'Flexão de Braço': 'Supino Reto com Halteres',
    'Flexão Declinada': 'Supino Inclinado com Halteres',
    'Flexão Inclinada': 'Supino Reto com Halteres',
    'Elevação de Pernas': 'Prancha',
    'Elevação de Pernas Suspenso': 'Abdominal Infra (Reverso)',
    'Superman': 'Prancha',
    'Abdominal Canivete (V-Up)': 'Abdominal Crunch',
    'Roda Abdominal (Ab Wheel)': 'Prancha',
    'Hollow Body Hold': 'Prancha',
    'Windshield Wiper': 'Russian Twist',
    'Extensão de Quadril (Coice)': 'Coice na Polia (Cabo)',

    # Substituições PRINCIPALMENTE por NÍVEL (Iniciante não pode fazer)
    'Barra Fixa': 'Puxada Alta (Lat Pulldown)',
    'Barra Fixa Supinada': 'Puxada Frontal com Pegada Fechada',
    'Mergulho no Banco': 'Tríceps Pulley',
    'Mergulho nas Paralelas': 'Tríceps Pulley',
    'Agachamento Búlgaro': 'Afundo Estacionário',
    'Sissy Squat': 'Cadeira Extensora',
    'Hack Squat': 'Leg Press 45°',
    'Rosca 21': 'Rosca Direta com Halteres',
    'Prancha com Elevação de Perna': 'Prancha',
    'Prancha com Toque no Ombro': 'Prancha',
    'Mountain Climber': 'Prancha',
    'Desenvolvimento Arnold': 'Desenvolvimento com Halteres (sentado)',
    'Elevação Lateral Inclinado': 'Elevação Lateral',
    'Pallof Press': 'Prancha Lateral',

    # Substituições por EQUIPAMENTO não disponível
    'Hip Thrust Unilateral': 'Elevação Pélvica',
    'Step Up': 'Afundo (Passada)',
    'Panturrilha em Pé (Máquina)': 'Panturrilha no Leg Press',
    'Panturrilha Sentado (Máquina)': 'Panturrilha no Leg Press',
    'Peck Deck (Voador)': 'Crucifixo com Halteres',
    'Crucifixo na Polia (Cross Over)': 'Crucifixo com Halteres',
    'Crucifixo Inclinado': 'Crucifixo com Halteres',
    'Supino na Máquina': 'Supino Reto com Halteres',
    'Pullover na Polia': 'Pullover com Halter',
    'Puxada com Triângulo': 'Puxada Alta (Lat Pulldown)',
    'Puxada Frontal com Pegada Fechada': 'Puxada Alta (Lat Pulldown)',
    'Remada na Polia Baixa': 'Remada Sentada (máquina)',
    'Desenvolvimento na Máquina': 'Desenvolvimento com Halteres (sentado)',
    'Elevação Lateral na Polia': 'Elevação Lateral',
    'Crucifixo Inverso na Máquina (Peck Deck Inverso)': 'Crucifixo Inverso com Halteres',
    'Rosca Scott (Banco Scott)': 'Rosca Concentrada',
    'Rosca na Polia Baixa': 'Rosca Direta com Halteres',
    'Tríceps Pulley com Corda': 'Tríceps Pulley',
    'Tríceps Unilateral na Polia': 'Tríceps Pulley',
    'Tríceps na Polia Alta (Overhead)': 'Tríceps Overhead (Francês em Pé)',
    'Abdominal na Polia': 'Abdominal Crunch',
    'Coice na Polia (Cabo)': 'Extensão de Quadril (Coice)',
    'Mesa Flexora Sentada': 'Mesa Flexora',
    'Encolhimento na Máquina': 'Encolhimento com Halteres',
}

# Grupos de exercícios por categoria (útil para busca e organização)
GRUPOS_MUSCULARES = {
    'Pernas': ['Quadríceps', 'Isquiotibiais', 'Glúteos', 'Panturrilhas', 'Adutores'],
    'Superior': ['Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps', 'Trapézio', 'Antebraço'],
    'Core': ['Abdômen', 'Lombar', 'Oblíquos']
}

# Dificuldade dos exercícios (para progressão)
NIVEL_DIFICULDADE = {
    'Iniciante': [
        'Leg Press 45°', 'Cadeira Extensora', 'Mesa Flexora', 'Mesa Flexora Sentada',
        'Elevação Pélvica', 'Abdução de Quadril', 'Glúteo Sapinho (Frog Pump)',
        'Panturrilha no Leg Press', 'Agachamento com Halteres', 'Agachamento Goblet',
        'Afundo (Passada)', 'Afundo Estacionário', 'Panturrilha com Halteres',
        'Supino Reto com Halteres', 'Supino Inclinado com Halteres', 'Crucifixo com Halteres',
        'Flexão Inclinada', 'Flexão de Braço', 'Peck Deck (Voador)', 'Supino na Máquina',
        'Puxada Alta (Lat Pulldown)', 'Remada Sentada (máquina)', 'Remada Unilateral (Serrote)',
        'Remada na Polia Baixa', 'Pullover na Polia',
        'Desenvolvimento com Halteres (sentado)', 'Elevação Lateral', 'Elevação Frontal',
        'Desenvolvimento na Máquina', 'Crucifixo Inverso com Halteres', 'Face Pull',
        'Rosca Direta com Halteres', 'Rosca Alternada', 'Rosca Martelo', 'Rosca Concentrada',
        'Rosca Scott (Banco Scott)', 'Rosca na Polia Baixa',
        'Tríceps Pulley', 'Tríceps Pulley com Corda', 'Tríceps Coice', 'Tríceps Unilateral na Polia',
        'Prancha', 'Prancha Lateral', 'Abdominal Crunch', 'Abdominal Infra (Reverso)',
        'Abdominal Bicicleta', 'Russian Twist', 'Dead Bug', 'Bird Dog',
        'Encolhimento com Halteres', 'Encolhimento com Barra',
        'Rosca Punho (Wrist Curl)', 'Dead Hang (Suspensão na Barra)'
    ],
    'Intermediário/Avançado': [
        'Agachamento com Barra', 'Agachamento Frontal', 'Agachamento Búlgaro', 'Hack Squat',
        'Sissy Squat', 'Stiff com Halteres', 'Stiff com Barra', 'Levantamento Terra Romeno',
        'Levantamento Terra', 'Good Morning', 'Hip Thrust Unilateral', 'Step Up',
        'Extensão de Quadril (Coice)', 'Coice na Polia (Cabo)', 'Abdução Deitado de Lado',
        'Supino Reto com Barra', 'Supino Inclinado com Barra', 'Supino Declinado',
        'Crucifixo Inclinado', 'Crucifixo na Polia (Cross Over)', 'Flexão Declinada',
        'Pullover com Halter',
        'Barra Fixa', 'Barra Fixa Supinada', 'Remada Curvada com Barra', 'Remada Curvada Supinada',
        'Remada com Halteres (Ambos os Braços)', 'Remada Cavalinho',
        'Desenvolvimento Militar com Barra', 'Desenvolvimento com Halteres (em pé)',
        'Desenvolvimento Arnold', 'Remada Alta', 'Elevação Lateral Inclinado',
        'Elevação Lateral na Polia', 'Elevação Frontal com Barra',
        'Crucifixo Inverso na Máquina (Peck Deck Inverso)',
        'Rosca Direta com Barra', 'Rosca Direta com Barra W', 'Rosca 21', 'Rosca Inversa',
        'Tríceps Testa', 'Tríceps Francês (Testa com Halteres)', 'Tríceps Overhead (Francês em Pé)',
        'Tríceps na Polia Alta (Overhead)', 'Mergulho no Banco', 'Mergulho nas Paralelas',
        'Supino Fechado',
        'Prancha com Elevação de Perna', 'Prancha com Toque no Ombro', 'Elevação de Pernas',
        'Elevação de Pernas Suspenso', 'Prancha Dinâmica (Mountain Climber)', 'Superman',
        'Pallof Press', 'Abdominal Canivete (V-Up)', 'Roda Abdominal (Ab Wheel)',
        'Hollow Body Hold', 'Windshield Wiper', 'Abdominal na Polia',
        'Encolhimento na Máquina', 'Farmer Walk (Caminhada do Fazendeiro)',
        'Rosca Punho Inversa'
    ]

}
WARMUP_ROUTINE = [
    {"nome": "Polichinelos", "duracao_s": 60, "descricao": "Movimento de saltar abrindo e fechando pernas e braços simultaneamente."},
    {"nome": "Corrida Estacionária (Joelho Alto)", "duracao_s": 60, "descricao": "Simule uma corrida no lugar, elevando bem os joelhos."},
    {"nome": "Rotação de Tronco", "duracao_s": 45, "descricao": "Em pé, gire o tronco suavemente para os lados, mantendo o quadril estável."},
    {"nome": "Círculos com os Braços (Para Frente)", "duracao_s": 30, "descricao": "Gire os braços estendidos para frente em círculos amplos."},
    {"nome": "Círculos com os Braços (Para Trás)", "duracao_s": 30, "descricao": "Gire os braços estendidos para trás em círculos amplos."},
    {"nome": "Agachamento sem Peso (Mobilidade)", "duracao_s": 60, "descricao": "Agache o mais fundo possível com boa forma, focando na mobilidade do quadril e tornozelo."},
    {"nome": "Alongamento Dinâmico de Isquiotibiais (Perna Reta)", "duracao_s": 45, "descricao": "Em pé, balance uma perna reta para frente e para trás controladamente."},
]

COOLDOWN_ROUTINE = [
    {"nome": "Alongamento Quadríceps (Em pé)", "duracao_s": 30, "descricao": "Segure o pé atrás, puxe o calcanhar em direção ao glúteo, mantendo joelhos juntos."},
    {"nome": "Alongamento Posterior Coxa (Sentado ou em pé)", "duracao_s": 30, "descricao": "Tente alcançar a ponta dos pés com as pernas estendidas, alongando a parte de trás das coxas."},
    {"nome": "Alongamento Glúteos (Figura 4 Deitado)", "duracao_s": 30, "descricao": "Deitado, cruze um tornozelo sobre o joelho oposto e puxe a coxa de baixo em direção ao peito."},
    {"nome": "Alongamento Peitoral (No batente da porta)", "duracao_s": 30, "descricao": "Apoie o antebraço no batente e gire o corpo suavemente para o lado oposto."},
    {"nome": "Alongamento Dorsal/Latíssimo (Ajoelhado)", "duracao_s": 30, "descricao": "Ajoelhe-se e estenda os braços à frente no chão, 'afundando' o peito em direção ao solo."},
    {"nome": "Alongamento Tríceps (Atrás da cabeça)", "duracao_s": 30, "descricao": "Leve um cotovelo acima e atrás da cabeça, puxe-o suavemente com a outra mão."},
    {"nome": "Alongamento Bíceps/Antebraço", "duracao_s": 30, "descricao": "Estenda um braço à frente com a palma para cima, puxe os dedos para baixo com a outra mão."},
]


# ---------------------------
# Plan serialization helpers
# ---------------------------
def plan_to_serial(plano: Optional[Dict[str, Any]]):
    if not plano:
        return None
    out = {}
    for k, v in plano.items():
        if isinstance(v, pd.DataFrame):
            # CORREÇÃO: Verificar se o DataFrame é válido antes de converter
            if verificar_dataframe_valido(v):
                out[k] = v.to_dict(orient='records')
            else:
                out[k] = []  # Retorna lista vazia se DataFrame inválido
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
                # CORREÇÃO: Verificar se a lista é válida antes de converter para DataFrame
                if len(v) > 0 and all(isinstance(item, dict) for item in v) and all('Exercício' in item for item in v):
                    df = pd.DataFrame(v)
                    # CORREÇÃO: Verificar se o DataFrame resultante é válido
                    if not df.empty and 'Exercício' in df.columns:
                        out[k] = df
                    else:
                        out[k] = v  # Mantém como lista se conversão falhar
                else:
                    out[k] = v  # Mantém como lista se vazia ou inválida
            except Exception:
                out[k] = v  # Mantém como lista se conversão falhar
        else:
            out[k] = v
    return out


# ---------------------------
# Firestore save/load (with spinner)
# ---------------------------

def carregar_dados_usuario_firebase(uid: str):
    if not uid:
        return

    try:
        with st.spinner("🔍 Carregando dados..."):
            doc = db.collection('usuarios').document(uid).get()
            time.sleep(0.2)

        if not doc.exists:
            return

        data = doc.to_dict()

        st.session_state['dados_usuario'] = data.get('dados_usuario')

        # CORREÇÃO: Validação mais robusta do plano carregado
        plano_carregado = serial_to_plan(data.get('plano_treino'))

        plano_valido = False
        plano_limpo = {}

        if plano_carregado and isinstance(plano_carregado, dict):
            for nome_treino, treino_data in plano_carregado.items():
                if treino_data is not None:
                    # Se for DataFrame
                    if isinstance(treino_data, pd.DataFrame):
                        if (not treino_data.empty and
                                'Exercício' in treino_data.columns and
                                len(treino_data) > 0):
                            plano_limpo[nome_treino] = treino_data
                            plano_valido = True

                    # Se for lista
                    elif isinstance(treino_data, list):
                        if (len(treino_data) > 0 and
                                all(isinstance(item, dict) for item in treino_data) and
                                all('Exercício' in item for item in treino_data)):
                            # Converter lista para DataFrame
                            df_treino = pd.DataFrame(treino_data)
                            if not df_treino.empty:
                                plano_limpo[nome_treino] = df_treino
                                plano_valido = True

        # Atribui o plano apenas se for válido
        if plano_valido and plano_limpo:
            st.session_state['plano_treino'] = plano_limpo
        else:
            st.session_state['plano_treino'] = None

        st.session_state['frequencia'] = [d.date() if isinstance(d, datetime) else d for d in
                                          data.get('frequencia', [])]
        st.session_state['historico_treinos'] = data.get('historico_treinos', [])
        st.session_state['fotos_progresso'] = data.get('fotos_progresso', [])
        st.session_state['medidas'] = data.get('medidas', [])
        st.session_state['feedbacks'] = data.get('feedbacks', [])
        st.session_state['metas'] = data.get('metas', [])
        st.session_state['role'] = data.get('role', 'free')

        # Settings com merge seguro
        settings_carregadas = data.get('settings', {})
        settings_atuais = st.session_state.get('settings', {'theme': 'light', 'notify_on_login': True})
        st.session_state['settings'] = {**settings_atuais, **settings_carregadas}

        st.session_state['ciclo_atual'] = data.get('ciclo_atual')

    except Exception as e:
        st.error(f"Erro ao carregar dados: {e}")
        # Em caso de erro, garante que o plano seja None
        st.session_state['plano_treino'] = None


def limpar_planos_antigos_firebase(uid: str):
    """Limpa todos os planos antigos do Firebase, mantendo apenas o plano atual"""
    if not uid or uid == 'demo-uid':
        return

    try:
        # Carrega os dados atuais
        doc_ref = db.collection('usuarios').document(uid)
        doc = doc_ref.get()

        if not doc.exists:
            return

        data = doc.to_dict()
        plano_atual = st.session_state.get('plano_treino')

        # CORREÇÃO: Verificar se o plano atual é válido
        if not plano_atual or not isinstance(plano_atual, dict):
            return

        # Filtra apenas os treinos válidos do plano atual
        plano_filtrado = {}
        for nome_treino, treino_data in plano_atual.items():
            if treino_data is not None:
                if isinstance(treino_data, pd.DataFrame):
                    if verificar_dataframe_valido(treino_data):
                        plano_filtrado[nome_treino] = treino_data
                elif isinstance(treino_data, list):
                    if len(treino_data) > 0:
                        plano_filtrado[nome_treino] = treino_data

        # Prepara o payload com apenas o plano atual válido
        plano_serial = plan_to_serial(plano_filtrado)

        # Atualiza apenas o campo plano_treino
        doc_ref.update({
            'plano_treino': plano_serial,
            'ultimo_save': datetime.now()
        })

        st.toast("✅ Planos antigos removidos, apenas o plano atual foi mantido!")

    except Exception as e:
        st.error(f"Erro ao limpar planos antigos: {e}")


def salvar_dados_usuario_firebase(uid: str):
    if not uid or uid == 'demo-uid':
        return

    try:
        with st.spinner("💾 Salvando dados no Firestore..."):
            doc = db.collection('usuarios').document(uid)

            # CORREÇÃO: Validação antes de salvar
            plano_para_salvar = st.session_state.get('plano_treino')
            plano_serial_valido = None

            if plano_para_salvar and isinstance(plano_para_salvar, dict):
                plano_filtrado = {}
                for nome_treino, treino_data in plano_para_salvar.items():
                    if treino_data is not None:
                        # Se for DataFrame
                        if isinstance(treino_data, pd.DataFrame):
                            if (not treino_data.empty and
                                    'Exercício' in treino_data.columns and
                                    len(treino_data) > 0):
                                plano_filtrado[nome_treino] = treino_data

                        # Se for lista
                        elif isinstance(treino_data, list):
                            if (len(treino_data) > 0 and
                                    all(isinstance(item, dict) for item in treino_data) and
                                    all('Exercício' in item for item in treino_data)):
                                plano_filtrado[nome_treino] = treino_data

                if plano_filtrado:
                    plano_serial_valido = plan_to_serial(plano_filtrado)

            # Prepara os outros dados
            freq = []
            for d in st.session_state.get('frequencia', []):
                if isinstance(d, (date, datetime)):
                    if isinstance(d, date) and not isinstance(d, datetime):
                        freq.append(datetime.combine(d, datetime.min.time()))
                    else:
                        freq.append(d)
                else:
                    freq.append(d) # Assume que já está no formato correto se não for date/datetime

            hist = []
            for t in st.session_state.get('historico_treinos', []):
                copy = dict(t)
                # Garante que a data no histórico seja datetime antes de salvar
                if 'data' in copy:
                    if isinstance(copy['data'], date) and not isinstance(copy['data'], datetime):
                         copy['data'] = datetime.combine(copy['data'], datetime.min.time())
                    elif isinstance(copy['data'], str): # Tenta converter string para datetime
                        try:
                            copy['data'] = datetime.fromisoformat(copy['data'].split('T')[0])
                        except ValueError:
                            # Se a conversão falhar, mantém como está ou define um padrão
                             pass # Ou logar um erro, dependendo da necessidade
                hist.append(copy)


            metas_save = []
            for m in st.session_state.get('metas', []):
                copy = dict(m)
                if 'prazo' in copy and isinstance(copy['prazo'], date):
                    copy['prazo'] = datetime.combine(copy['prazo'], datetime.min.time())
                elif 'prazo' in copy and isinstance(copy['prazo'], str): # Converte string
                     try:
                        copy['prazo'] = datetime.fromisoformat(copy['prazo'].split('T')[0])
                     except ValueError:
                         copy['prazo'] = None # Define como None se a string for inválida
                metas_save.append(copy)

            fotos_save = []
            for f in st.session_state.get('fotos_progresso', []):
                copy = dict(f)
                # Mantém a data da foto como string ISO formatada
                if 'data' in copy and isinstance(copy['data'], date):
                    copy['data'] = copy['data'].isoformat()
                # Garante que 'timestamp' seja datetime
                if 'timestamp' in copy and isinstance(copy['timestamp'], str):
                     try:
                         copy['timestamp'] = datetime.fromisoformat(copy['timestamp'])
                     except ValueError:
                         pass # Mantém como string se inválido
                fotos_save.append(copy)

            payload = {
                'dados_usuario': st.session_state.get('dados_usuario'),
                'plano_treino': plano_serial_valido,  # APENAS o plano atual válido
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
                'ultimo_save': datetime.now(timezone.utc), # Usa UTC
                'xp_total': st.session_state.get('xp_total', 0), # Inclui XP
                'xp_semanal': st.session_state.get('xp_semanal', 0), # Inclui XP
                'ultima_verificacao_semanal': st.session_state.get('ultima_verificacao_semanal') # Inclui data de reset
            }

            # ==================== CORREÇÃO AQUI ====================
            # Removemos o 'merge=True'. Agora o set vai SOBRESCREVER o documento.
            doc.set(payload)
            # =======================================================
            time.sleep(0.4)

    except Exception as e:
        st.error("Erro ao salvar no Firestore:")
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

        # Define os dados do novo usuário
        user_data = {
            'email': email, 'username': nome, 'dados_usuario': {'nome': nome},
            'plano_treino': None, 'frequencia': [], 'historico_treinos': [],
            'historico_peso': [], 'metas': [], 'fotos_progresso': [], 'medidas': [],
            'feedbacks': [], 'ciclo_atual': None,
            'role': 'free',
            'password_hash': sha256(senha),
            'data_criacao': datetime.now(timezone.utc),  # <-- CORRIGIDO

            # =====================================
            # =   ADIÇÃO PARA GAMIFICAÇÃO AQUI    =
            # =====================================
            'xp_total': 0,
            'xp_semanal': 0,
            'ultima_verificacao_semanal': datetime.now(timezone.utc)  # <-- CORRIGIDO
            # =====================================
        }

        # Salva o novo usuário no Firestore
        db.collection('usuarios').document(uid).set(user_data)

        return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"


def verificar_credenciais_firebase(username_or_email: str, senha: str) -> (bool, str):
    if username_or_email == 'demo' and senha == 'demo123':
        st.session_state['user_uid'] = 'demo-uid'
        st.session_state['usuario_logado'] = 'Demo'

        # Carregar dados demo
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

            # ⬅️ SALVAR COOKIE É FEITO NA FUNÇÃO render_auth AGORA
            return True, f"Bem-vindo(a), {st.session_state['usuario_logado']}!"
        else:
            return False, "Senha incorreta."

    except auth.UserNotFoundError:
        return False, "Usuário não encontrado."
    except Exception as e:
        return False, f"Erro ao autenticar: {e}"



def fazer_logout():
    """Função dedicada para fazer logout de forma limpa"""
    try:
        # LIMPAR COOKIES (IMPORTANTE!)
        cookies['user_uid'] = ""
        cookies.save()

        # Lista de chaves que devem ser mantidas
        keys_to_keep = ['db']  # Mantém apenas a conexão com Firebase

        # Limpar session state de forma segura
        current_keys = list(st.session_state.keys())
        for key in current_keys:
            if key not in keys_to_keep:
                del st.session_state[key]

        # Garantir que os defaults sejam resetados
        ensure_session_defaults()

        print("✅ Logout realizado com sucesso - Cookies limpos")

    except Exception as e:
        print(f"❌ Erro durante logout: {e}")
        # Forçar limpeza mesmo com erro
        st.session_state.clear()
        ensure_session_defaults()

# ---------------------------
# Periodization & Notifications
# ---------------------------
def verificar_periodizacao(num_treinos: int):
    TREINOS_POR_CICLO = 20

    # ✅ LÓGICA CORRIGIDA: Cálculo mais intuitivo
    ciclo = (num_treinos - 1) // TREINOS_POR_CICLO  # Ciclo atual (0-based)
    fase_idx = ciclo % 3  # 0=Hipertrofia, 1=Força, 2=Resistência
    treinos_no_ciclo_atual = (num_treinos - 1) % TREINOS_POR_CICLO + 1
    treinos_restantes = TREINOS_POR_CICLO - treinos_no_ciclo_atual

    fases = [
        {'nome': 'Hipertrofia', 'series': '3-4', 'reps': '8-12', 'descanso': '60-90s', 'cor': '#FF6B6B'},
        {'nome': 'Força', 'series': '4-5', 'reps': '4-6', 'descanso': '120-180s', 'cor': '#4ECDC4'},
        {'nome': 'Resistência', 'series': '2-3', 'reps': '15-20', 'descanso': '30-45s', 'cor': '#95E1D3'},
    ]

    return {
        'fase_atual': fases[fase_idx],
        'treinos_restantes': treinos_restantes,
        'treinos_no_ciclo_atual': treinos_no_ciclo_atual,
        'proxima_fase': fases[(fase_idx + 1) % 3],
        'numero_ciclo': ciclo + 1  # Ciclo 1-based para exibição
    }

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

    # ✅ LÓGICA CORRIGIDA: Periodização a cada 20 treinos
    num_treinos = len(set(st.session_state.get('frequencia', [])))

    # Só verifica periodização se tiver pelo menos 1 treino
    if num_treinos > 0:
        info = verificar_periodizacao(num_treinos)

        # ✅ CORREÇÃO: Trocar de ciclo quando for múltiplo de 20
        # Exemplo: 20, 40, 60, 80... treinos
        if num_treinos % 20 == 0:
            ciclo_anterior = st.session_state.get('ciclo_atual')
            ciclo_novo = info['numero_ciclo']

            if ciclo_anterior != ciclo_novo:
                # NOVO CICLO DETECTADO!
                notifs.append({'tipo': 'nova_fase',
                               'msg': f"👏 Novo ciclo iniciado: {info['fase_atual']['nome']} (Ciclo {ciclo_novo})"})
                st.session_state['ciclo_atual'] = ciclo_novo

                # ✅ REGENERAR PLANO COM A NOVA FASE
                if dados:
                    try:
                        novo_plano = gerar_plano_personalizado(dados, info['fase_atual'])
                        if novo_plano:
                            st.session_state['plano_treino'] = novo_plano
                            # Salvar no Firebase
                            uid = st.session_state.get('user_uid')
                            if uid:
                                salvar_dados_usuario_firebase(uid)
                            notifs.append({'tipo': 'plano_ajustado',
                                           'msg': f'Seu plano foi ajustado para a fase de {info["fase_atual"]["nome"]}!'})
                    except Exception as e:
                        st.error(f"Erro ao regenerar plano: {e}")

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
# [NOVA] Função de callback para navegação (necessária para os botões VIP)
def navigate_to_page(page_name):
    """Atualiza o session_state para mudar a página no próximo rerun."""
    st.session_state['selected_page'] = page_name


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


def gerar_plano_personalizado(dados_usuario: Dict[str, Any], fase_atual: Optional[Dict] = None,
                              force_new: bool = False) -> Dict:
    nivel = dados_usuario.get('nivel', 'Iniciante')
    dias = dados_usuario.get('dias_semana', 3)
    objetivo = dados_usuario.get('objetivo', 'Hipertrofia')
    restricoes_usr = dados_usuario.get('restricoes', [])
    sexo = dados_usuario.get('sexo', 'Masculino')  # <-- VARIÁVEL AGORA É USADA

    # ========== SEED MAIS CONSISTENTE ==========
    if not force_new:
        user_uid = st.session_state.get('user_uid', 'default')
        # Adiciona mais informações para tornar o seed mais único
        seed_string = f"{user_uid}_{nivel}_{dias}_{objetivo}_{sexo}_{'-'.join(sorted(restricoes_usr))}"
        # Usa hash mais robusto
        import hashlib
        seed_value = int(hashlib.sha256(seed_string.encode()).hexdigest()[:8], 16) % (2 ** 32)
        random.seed(seed_value)
        # st.write(f"DEBUG: Seed usado: {seed_value}")  # Descomente para debug
    else:
        random.seed()  # Seed aleatório para forçar novo plano
    # ===========================================

    # Define séries/reps/descanso base
    if fase_atual:
        series_base_str = fase_atual['series']
        reps_base = fase_atual['reps']
        descanso_base = fase_atual['descanso']
    else:
        if objetivo == 'Hipertrofia':
            series_base_str, reps_base, descanso_base = ('3-4' if nivel != 'Iniciante' else '3'), '8-12', '60-90s'
        elif objetivo == 'Emagrecimento':
            series_base_str, reps_base, descanso_base = '3', '12-15', '45-60s'
        else:
            series_base_str, reps_base, descanso_base = '3', '15-20', '30-45s'

    # Determina o número de séries
    series_parts = series_base_str.split('-')
    series_final = series_parts[0] if nivel == 'Iniciante' else series_parts[-1]
    if not series_final.isdigit(): series_final = '3'

    # Função selecionar_exercicios
    def selecionar_exercicios(grupos: List[str], n_compostos: int, n_isolados: int, excluir: List[str] = []) -> List[
        Dict]:
        exercicios_selecionados = []
        candidatos_validos = []

        for ex_nome, ex_data in EXERCICIOS_DB.items():
            niveis_permitidos = ex_data.get('niveis_permitidos', ['Iniciante', 'Intermediário/Avançado'])
            if nivel not in niveis_permitidos: continue

            if ex_data.get('grupo') in grupos and ex_nome not in excluir:
                exercicio_tem_restricao = any(r in ex_data.get('restricoes', []) for r in restricoes_usr)
                if exercicio_tem_restricao:
                    substituto = EXERCISE_SUBSTITUTIONS.get(ex_nome)
                    if substituto and substituto not in excluir:
                        sub_details = EXERCICIOS_DB.get(substituto, {})
                        sub_niveis_permitidos = sub_details.get('niveis_permitidos',
                                                                ['Iniciante', 'Intermediário/Avançado'])
                        if nivel in sub_niveis_permitidos and substituto not in candidatos_validos and not any(
                                r in sub_details.get('restricoes', []) for r in restricoes_usr):
                            candidatos_validos.append(substituto)
                elif nivel in niveis_permitidos and ex_nome not in candidatos_validos:
                    candidatos_validos.append(ex_nome)

        candidatos = list(set(candidatos_validos))
        random.shuffle(candidatos)
        compostos_selecionados = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] == 'Composto']
        isolados_selecionados = [ex for ex in candidatos if EXERCICIOS_DB[ex]['tipo'] != 'Composto']
        compostos_finais = compostos_selecionados[:n_compostos]
        isolados_finais = isolados_selecionados[:n_isolados]
        exercicios_finais = compostos_finais + isolados_finais

        total_desejado = n_compostos + n_isolados
        if len(exercicios_finais) < total_desejado:
            faltantes = total_desejado - len(exercicios_finais)
            if len(isolados_finais) < n_isolados and len(compostos_selecionados) > len(compostos_finais):
                extras_c = [ex for ex in compostos_selecionados if ex not in exercicios_finais][:faltantes]
                exercicios_finais.extend(extras_c)
                faltantes -= len(extras_c)
            if faltantes > 0 and len(compostos_finais) < n_compostos and len(isolados_selecionados) > len(
                    isolados_finais):
                extras_i = [ex for ex in isolados_selecionados if ex not in exercicios_finais][:faltantes]
                exercicios_finais.extend(extras_i)

        exercicios_finais = exercicios_finais[:total_desejado]

        for ex in exercicios_finais:
            exercicios_selecionados.append(
                {'Exercício': ex, 'Séries': series_final, 'Repetições': reps_base, 'Descanso': descanso_base})

        return exercicios_selecionados if exercicios_finais else []

    # --- LÓGICA DE GERAÇÃO ---
    plano = {}
    grupos_todos = ['Pernas', 'Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps', 'Core', 'Trapézio', 'Antebraço']
    grupos_superiores = ['Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps', 'Trapézio', 'Antebraço']
    grupos_inferiores = ['Pernas', 'Core']
    grupos_push = ['Peito', 'Ombros', 'Tríceps']
    grupos_pull = ['Costas', 'Bíceps', 'Trapézio', 'Antebraço']
    grupos_legs = ['Pernas']

    # ================== NOVOS GRUPOS PARA FOCO ==================
    grupos_inferiores_foco = ['Pernas', 'Core']  # Usar (3c, 2i)
    grupos_superiores_foco = ['Peito', 'Costas', 'Ombros', 'Bíceps', 'Tríceps']  # Usar (3c, 2i)
    # ==========================================================

    if nivel == 'Iniciante':
        if dias == 1:
            # Full body é unissex
            plano['Treino: Full Body'] = selecionar_exercicios(grupos_todos, 3, 3)

        elif dias == 2:
            if sexo == 'Feminino':
                # Foco maior em inferiores
                plano['Treino A: Inferiores'] = selecionar_exercicios(grupos_inferiores, 3, 2)
                plano['Treino B: Superiores'] = selecionar_exercicios(grupos_superiores, 2, 3)
            else:
                # Foco maior em superiores (lógica original invertida)
                plano['Treino A: Superiores'] = selecionar_exercicios(grupos_superiores, 3, 2)
                plano['Treino B: Inferiores'] = selecionar_exercicios(grupos_inferiores, 2, 3)

        elif dias == 3:
            # Full body 3x é uma ótima estratégia unissex para iniciantes
            fb1 = selecionar_exercicios(grupos_todos, 3, 2)
            fb2 = selecionar_exercicios(grupos_todos, 3, 2, excluir=[ex['Exercício'] for ex in fb1])
            fb3 = selecionar_exercicios(grupos_todos, 3, 2, excluir=[ex['Exercício'] for ex in fb1 + fb2])
            plano['Dia 1: Full Body A'] = fb1
            plano['Dia 2: Full Body B'] = fb2 if fb2 else fb1
            plano['Dia 3: Full Body C'] = fb3 if fb3 else fb2 if fb2 else fb1

        elif dias == 4:
            # Divisão Upper/Lower é mais eficiente que o A/B antigo
            if sexo == 'Feminino':
                lower_a = selecionar_exercicios(grupos_inferiores_foco, 3, 2)
                upper_a = selecionar_exercicios(grupos_superiores, 2, 3)
                plano['Dia 1: Inferiores A'] = lower_a
                plano['Dia 2: Superiores A'] = upper_a
                plano['Dia 3: Inferiores B'] = selecionar_exercicios(grupos_inferiores_foco, 3, 2,
                                                                     excluir=[ex['Exercício'] for ex in
                                                                              lower_a]) or lower_a
                plano['Dia 4: Superiores B'] = selecionar_exercicios(grupos_superiores, 2, 3,
                                                                     excluir=[ex['Exercício'] for ex in
                                                                              upper_a]) or upper_a
            else:
                upper_a = selecionar_exercicios(grupos_superiores_foco, 3, 2)
                lower_a = selecionar_exercicios(grupos_inferiores, 2, 3)
                plano['Dia 1: Superiores A'] = upper_a
                plano['Dia 2: Inferiores A'] = lower_a
                plano['Dia 3: Superiores B'] = selecionar_exercicios(grupos_superiores_foco, 3, 2,
                                                                     excluir=[ex['Exercício'] for ex in
                                                                              upper_a]) or upper_a
                plano['Dia 4: Inferiores B'] = selecionar_exercicios(grupos_inferiores, 2, 3,
                                                                     excluir=[ex['Exercício'] for ex in
                                                                              lower_a]) or lower_a

        elif dias == 5:
            # Foco LULUL (F) vs ULULU (M)
            upper_a = selecionar_exercicios(grupos_superiores, 2, 3)
            lower_a = selecionar_exercicios(grupos_inferiores_foco, 3, 2)
            upper_b = selecionar_exercicios(grupos_superiores, 2, 3, excluir=[ex['Exercício'] for ex in upper_a])
            lower_b = selecionar_exercicios(grupos_inferiores_foco, 3, 2, excluir=[ex['Exercício'] for ex in lower_a])

            if sexo == 'Feminino':
                plano['Dia 1: Inferiores A'] = lower_a
                plano['Dia 2: Superiores A'] = upper_a
                plano['Dia 3: Inferiores B'] = lower_b
                plano['Dia 4: Superiores B'] = upper_b if upper_b else upper_a
                plano['Dia 5: Inferiores A (Repete)'] = lower_a
            else:
                plano['Dia 1: Superiores A'] = upper_a
                plano['Dia 2: Inferiores A'] = lower_a
                plano['Dia 3: Superiores B'] = upper_b if upper_b else upper_a
                plano['Dia 4: Inferiores B'] = lower_b
                plano['Dia 5: Superiores A (Repete)'] = upper_a

        elif dias >= 6:
            # ABC-ABC
            abc_a = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 3, 2)
            abc_b = selecionar_exercicios(['Costas', 'Bíceps'], 3, 2)
            abc_c = selecionar_exercicios(['Pernas', 'Core'], 3, 2)

            if sexo == 'Feminino':
                # Foco maior em Pernas/Core
                abc_c = selecionar_exercicios(['Pernas', 'Core'], 3, 3)  # Mais volume em pernas

            plano['Dia 1: Treino A1'] = abc_a
            plano['Dia 2: Treino B1'] = abc_b
            plano['Dia 3: Treino C1 (Pernas)'] = abc_c
            plano['Dia 4: Treino A2'] = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 3, 2,
                                                              excluir=[ex['Exercício'] for ex in abc_a]) or abc_a
            plano['Dia 5: Treino B2'] = selecionar_exercicios(['Costas', 'Bíceps'], 3, 2,
                                                              excluir=[ex['Exercício'] for ex in abc_b]) or abc_b
            plano['Dia 6: Treino C2 (Pernas)'] = selecionar_exercicios(['Pernas', 'Core'], 3, 3,
                                                                       excluir=[ex['Exercício'] for ex in
                                                                                abc_c]) or abc_c

    elif nivel == 'Intermediário/Avançado':
        if dias == 1:
            plano['Treino: Full Body Intenso'] = selecionar_exercicios(grupos_todos, 4, 1)
        elif dias == 2:
            plano['Treino A: Full Body Foco Força'] = selecionar_exercicios(grupos_todos, 4, 1)
            plano['Treino B: Full Body Foco Volume'] = selecionar_exercicios(grupos_todos, 2, 3)

        elif dias == 3:
            # PPL
            push = selecionar_exercicios(grupos_push, 3, 2)
            pull = selecionar_exercicios(grupos_pull, 3, 2)
            legs = selecionar_exercicios(grupos_legs + ['Core'], 3, 2)

            if sexo == 'Feminino':
                legs = selecionar_exercicios(grupos_legs + ['Core'], 3, 3)  # Mais volume em pernas

            plano['Dia 1: Push'] = push
            plano['Dia 2: Pull'] = pull
            plano['Dia 3: Legs'] = legs

        elif dias == 4:
            # Upper/Lower 2x
            upper_a = selecionar_exercicios(grupos_superiores, 3, 2)
            lower_a = selecionar_exercicios(grupos_inferiores, 3, 2)
            upper_b = selecionar_exercicios(grupos_superiores, 2, 3, excluir=[ex['Exercício'] for ex in upper_a])
            lower_b = selecionar_exercicios(grupos_inferiores, 2, 3, excluir=[ex['Exercício'] for ex in lower_a])

            if sexo == 'Feminino':
                # Troca ênfase para Lower/Upper
                plano['Dia 1: Inferiores (Foco Força)'] = lower_a
                plano['Dia 2: Superiores (Foco Volume)'] = upper_b
                plano['Dia 3: Inferiores (Foco Volume)'] = lower_b
                plano['Dia 4: Superiores (Foco Força)'] = upper_a
            else:
                plano['Dia 1: Superiores (Foco Força)'] = upper_a
                plano['Dia 2: Inferiores (Foco Força)'] = lower_a
                plano['Dia 3: Superiores (Foco Volume)'] = upper_b
                plano['Dia 4: Inferiores (Foco Volume)'] = lower_b

        elif dias == 5:
            if sexo == 'Feminino':
                plano['Dia 1: Inferiores (Foco Glúteo)'] = selecionar_exercicios(grupos_inferiores_foco, 3, 2)
                plano['Dia 2: Superiores A'] = selecionar_exercicios(grupos_superiores, 2, 3)
                plano['Dia 3: Inferiores (Foco Quad)'] = selecionar_exercicios(grupos_inferiores_foco, 3, 2)
                plano['Dia 4: Superiores B'] = selecionar_exercicios(grupos_superiores, 2, 3)
                plano['Dia 5: Full Body (Metabólico)'] = selecionar_exercicios(grupos_todos, 2, 3)
            else:
                plano['Dia 1: Push'] = selecionar_exercicios(grupos_push, 3, 2)
                plano['Dia 2: Pull'] = selecionar_exercicios(grupos_pull, 3, 2)
                plano['Dia 3: Legs'] = selecionar_exercicios(grupos_legs, 3, 2)
                plano['Dia 4: Upper (Volume)'] = selecionar_exercicios(grupos_superiores, 2, 3)
                plano['Dia 5: Lower/Core (Volume)'] = selecionar_exercicios(grupos_inferiores, 3, 2)

        elif dias >= 6:
            # PPL 2x
            a1 = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 3, 2)
            b1 = selecionar_exercicios(['Costas', 'Bíceps', 'Trapézio'], 3, 2)
            c1 = selecionar_exercicios(['Pernas', 'Core'], 3, 2)

            if sexo == 'Feminino':
                c1 = selecionar_exercicios(['Pernas', 'Core'], 3, 3)  # Mais volume em pernas

            plano['Dia 1: Push A'] = a1
            plano['Dia 2: Pull A'] = b1
            plano['Dia 3: Legs A'] = c1
            plano['Dia 4: Push B'] = selecionar_exercicios(['Peito', 'Ombros', 'Tríceps'], 3, 2,
                                                           excluir=[ex['Exercício'] for ex in a1]) or a1
            plano['Dia 5: Pull B'] = selecionar_exercicios(['Costas', 'Bíceps', 'Trapézio'], 3, 2,
                                                           excluir=[ex['Exercício'] for ex in b1]) or b1
            plano['Dia 6: Legs B'] = selecionar_exercicios(['Pernas', 'Core'], 3, 3,
                                                           excluir=[ex['Exercício'] for ex in c1]) or c1

    plano_final = {}
    for nome, exercicios_lista in plano.items():
        plano_final[nome] = exercicios_lista if exercicios_lista else []
    return plano_final


# ---------------------------
# Pages
# ---------------------------
def render_auth():
    # VERIFICAR SE JÁ EXISTE USUÁRIO LOGADO NOS COOKIES
    user_uid_from_cookie = cookies.get('user_uid')

    if user_uid_from_cookie and user_uid_from_cookie != "":  # ⬅️ VERIFICA SE TEM COOKIE VÁLIDO
        try:
            # Tentar carregar os dados do usuário do cookie
            st.session_state['user_uid'] = user_uid_from_cookie
            doc = db.collection('usuarios').document(user_uid_from_cookie).get()

            if doc.exists:
                data = doc.to_dict()
                st.session_state['usuario_logado'] = data.get('username') or data.get('email', 'Usuário')
                st.session_state['dados_usuario'] = data.get('dados_usuario', {})
                st.session_state['role'] = data.get('role', 'free')

                # Carregar outros dados
                carregar_dados_usuario_firebase(user_uid_from_cookie)

                st.success(f"👋 Bem-vindo de volta, {st.session_state['usuario_logado']}!")
                st.rerun()
                return
        except Exception as e:
            # Se der erro ao carregar do cookie, continuar com login normal
            print(f"Erro ao carregar do cookie: {e}")
            pass

    # SE NÃO TEM COOKIE VÁLIDO, MOSTRAR TELA DE LOGIN NORMAL
    show_logo_center()
    st.markdown("---")

    tab_login, tab_cad = st.tabs(["🔑 Login", "📝 Cadastro"])

    with tab_login:
        with st.form("form_login"):
            username = st.text_input("E-mail ou 'demo'")
            senha = st.text_input("Senha", type='password')
            lembrar = st.checkbox("Lembrar-me", value=True)  # ⬅️ OPÇÃO DE LEMBRAR LOGIN

            col1, col2 = st.columns([3, 1])
            with col2:
                if st.form_submit_button("👁️ Modo Demo"):
                    ok, msg = verificar_credenciais_firebase('demo', 'demo123')
                    if ok:
                        # Salvar cookie para demo também
                        cookies['user_uid'] = 'demo-uid'
                        cookies.save()
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            if st.form_submit_button("Entrar"):
                if not username or not senha:
                    st.error("Preencha username e senha.")
                else:
                    ok, msg = verificar_credenciais_firebase(username.strip(), senha)
                    if ok:
                        # SALVAR COOKIE SE O USUÁRIO QUISER LEMBRAR
                        if lembrar:
                            cookies['user_uid'] = st.session_state.get('user_uid', '')
                            cookies.save()
                        st.success(msg)
                        st.rerun()
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
                        # Salvar cookie automaticamente para novos usuários
                        cookies['user_uid'] = st.session_state.get('user_uid', '')
                        cookies.save()
                        st.success(msg)
                        st.info("Faça login agora.")
                    else:
                        st.error(msg)

    st.stop()


def fazer_logout():
    """Função dedicada para fazer logout de forma limpa"""
    try:
        # Limpar cookies
        if 'user_uid' in cookies:
            cookies['user_uid'] = ""
            cookies.save()

        # Lista de chaves que devem ser mantidas
        keys_to_keep = ['db']  # Mantém apenas a conexão com Firebase

        # Limpar session state de forma segura
        current_keys = list(st.session_state.keys())
        for key in current_keys:
            if key not in keys_to_keep:
                del st.session_state[key]

        # Garantir que os defaults sejam resetados
        ensure_session_defaults()

        # Debug opcional (pode remover depois)
        print("✅ Logout realizado com sucesso")

    except Exception as e:
        print(f"❌ Erro durante logout: {e}")
        # Forçar limpeza mesmo com erro
        st.session_state.clear()
        ensure_session_defaults()

# [MODIFICADO] Função render_main com a nova "Biblioteca VIP"
def render_main():
    # ========== VERIFICAÇÃO DO SETTINGS ==========
    if 'settings' not in st.session_state:
        st.session_state['settings'] = {'theme': 'light', 'notify_on_login': True}

    # ========== VERIFICAR SE USUÁRIO NÃO ESTÁ LOGADO ==========
    if not st.session_state.get('usuario_logado'):
        render_auth()
        st.stop()

    # ==========================================================
    # =        MODIFICAÇÃO: CHAMAR O RESET SEMANAL             =
    # ==========================================================
    user_uid = st.session_state.get('user_uid')
    if user_uid:
        verificar_reset_semanal(user_uid)
    # ==========================================================

    # Verifica os modos ativos
    if st.session_state.get('warmup_in_progress', False):
        render_warmup_session()
        st.stop()
    elif st.session_state.get('workout_in_progress', False):
        render_workout_session()
        st.stop()
    elif st.session_state.get('cooldown_in_progress', False):
        render_cooldown_session()
        st.stop()

    check_notifications_on_open()

    # ========== HEADER PRINCIPAL ==========
    col_header1, col_header2, col_header3 = st.columns([3, 5, 2])

    with col_header1:
        st.markdown("<h1 style='margin: 0;'>🏋️ FitPro</h1>", unsafe_allow_html=True)
        st.caption(f"👤 {st.session_state.get('usuario_logado')}")

        user_role = st.session_state.get('role', 'free')
        if user_role in ['admin', 'vip']:
            st.success(f"⭐ {user_role.upper()}", icon="⭐")

    with col_header3:
        st.write("")  # Espaçamento

        # Botão de sair com CHAVE ESTÁTICA
        if st.button("🚪 Sair", use_container_width=True, key="main_btn_sair"):
            # Salvar dados antes de sair
            uid = st.session_state.get('user_uid')
            if uid and uid != 'demo-uid':
                salvar_dados_usuario_firebase(uid)

            # Fazer logout
            fazer_logout()

            # Delay para o CookieManager
            time.sleep(0.5)

            st.rerun()

    # ========== MENU DE NAVEGAÇÃO ==========
    st.markdown("---")

    # ==========================================================
    # =        MODIFICAÇÃO: ADICIONAR "RANKING" E "CONQUISTAS" =
    # ==========================================================
    # Define a lista base de páginas
    pages = [
        "Dashboard", "Ranking", "Rede Social", "Buscar Usuários", "Questionário", "Meu Treino",
        "Registrar Treino", "Progresso", "Fotos", "Comparar Fotos", "Medidas",
        "Planejamento Semanal", "Metas", "Nutrição", "Busca",
        "Export/Backup", "Solicitar VIP"
    ]
    # (Opcional: Adicionar página "Minhas Conquistas" se você criá-la)
    # ==========================================================

    # Adiciona a Biblioteca VIP dinamicamente
    user_role = st.session_state.get('role', 'free')
    if user_role in ['vip', 'admin']:
        pages.insert(6, "Biblioteca VIP")  # Ajustar índice se necessário

    if user_role == 'admin':
        pages.append("Admin")

    if 'selected_page' not in st.session_state or st.session_state['selected_page'] not in pages:
        st.session_state['selected_page'] = "Dashboard"

    # Chave estática para o selectbox de navegação
    nav_key = "main_nav_select"

    def on_nav_change():
        selected = st.session_state.get(nav_key)
        if selected and selected in pages:
            st.session_state.selected_page = selected
            # st.rerun() # O rerun já é acionado pelo on_change

    selected_page = st.selectbox(
        "Navegação",
        pages,
        index=pages.index(st.session_state['selected_page']),
        key=nav_key,
        on_change=on_nav_change
    )

    # ========== CONFIGURAÇÕES ==========
    with st.expander("⚙️ Configurações", icon="⚙️"):
        col_config1, col_config2 = st.columns(2)
        with col_config1:
            theme_key = "main_theme_select"  # <-- CHAVE ESTÁTICA
            theme = st.selectbox("Tema", ["light", "dark"],
                                 index=0 if st.session_state['settings'].get('theme', 'light') == 'light' else 1,
                                 key=theme_key)
            st.session_state['settings']['theme'] = theme
        with col_config2:
            notify_key = "main_notify_check"  # <-- CHAVE ESTÁTICA
            notify_on_open = st.checkbox("Notificações ao abrir",
                                         value=st.session_state['settings'].get('notify_on_login', True),
                                         key=notify_key)
            st.session_state['settings']['notify_on_login'] = notify_on_open

    st.markdown("---")

    # ==========================================================
    # =        MODIFICAÇÃO: ADICIONAR ROTA DO RANKING          =
    # ==========================================================
    page_map = {
        "Dashboard": render_dashboard,
        "Ranking": render_ranking,  # <-- NOVA ROTA
        "Rede Social": render_rede_social,
        "Buscar Usuários": render_buscar_usuarios,
        "Questionário": render_questionario,
        "Meu Treino": render_meu_treino,
        "Biblioteca VIP": render_vip_library,
        "Registrar Treino": render_registrar_treino,
        "Progresso": render_progresso,
        "Fotos": render_fotos,
        "Comparar Fotos": render_comparar_fotos,
        "Medidas": render_medidas,
        "Planejamento Semanal": render_planner,
        "Metas": render_metas,
        "Nutrição": render_nutricao_gated,
        "Busca": render_busca,
        "Export/Backup": render_export_backup,
        "Solicitar VIP": render_solicitar_vip,
        "Admin": render_admin_panel,
    }
    # ==========================================================

    render_func = page_map.get(st.session_state.selected_page, lambda: st.write("Página em desenvolvimento."))

    try:
        render_func()
    except Exception as e:
        st.error(f"Erro ao renderizar a página '{st.session_state.selected_page}': {e}")
        st.info("Tente recarregar a página ou voltar para o Dashboard.")
        error_key = "main_error_btn"  # <-- CHAVE ESTÁTICA
        if st.button("Voltar para Dashboard", key=error_key):
            st.session_state.selected_page = "Dashboard"
            st.rerun()


def render_admin_panel():
    st.title("👑 Painel Admin")
    st.warning("Use com cuidado — ações afetam usuários reais.")

    # ==========================================================
    # =        SEÇÃO PARA ATUALIZAÇÃO AUTOMÁTICA (MIGRAÇÃO)    =
    # ==========================================================
    st.markdown("---")
    st.subheader("🚀 Migração de Dados (Gamificação)")
    st.info(
        "Clique no botão abaixo para adicionar os campos 'xp_total: 0' e 'xp_semanal: 0' a todos os usuários que ainda não os possuem. Isso só precisa ser executado UMA VEZ.")

    if st.button("Executar Atualização de Usuários para Gamificação", type="primary", key="admin_btn_migracao_xp"):
        try:
            with st.spinner("Buscando todos os usuários..."):
                users_ref = db.collection('usuarios').stream()
                users_list = list(users_ref)  # Pega todos

            batch = db.batch()
            count_atualizados = 0

            with st.spinner(f"Processando {len(users_list)} usuários..."):
                for user_doc in users_list:
                    user_data = user_doc.to_dict()

                    # Verifica se o campo já existe para não sobrescrever
                    if 'xp_total' not in user_data:
                        # Se não existe, adiciona a atualização ao "batch"
                        doc_ref = db.collection('usuarios').document(user_doc.id)
                        batch.update(doc_ref, {
                            'xp_total': 0,
                            'xp_semanal': 0,
                            # ==================== CORREÇÃO AQUI ====================
                            'ultima_verificacao_semanal': datetime(2020, 1, 1, tzinfo=timezone.utc)  # Data antiga aware
                            # =======================================================
                        })
                        count_atualizados += 1

            # Envia todas as atualizações para o Firebase de uma só vez
            if count_atualizados > 0:
                batch.commit()
                st.success(f"Sucesso! {count_atualizados} usuários foram atualizados para o sistema de gamificação.")
            else:
                st.info("Nenhum usuário precisava de atualização. Todos já possuem os campos de XP.")

        except Exception as e:
            st.error(f"Ocorreu um erro durante a migração: {e}")
    # ==========================================================
    # =                  FIM DA NOVA SEÇÃO                     =
    # ==========================================================

    # --- Seção para Solicitações VIP Pendentes ---
    st.markdown("---")
    st.subheader("📬 Solicitações de Acesso VIP Pendentes")

    try:
        # Corrigido para usar o 'filter' keyword argument e evitar o UserWarning
        vip_requests_ref = db.collection('solicitacoes_vip').where(
            filter=firestore.FieldFilter('status', '==', 'pendente')
        ).order_by('timestamp').stream()

        pending_requests = list(vip_requests_ref)  # Converte para lista

        if not pending_requests:
            st.info("Nenhuma solicitação VIP pendente no momento.")
        else:
            st.write(f"Total de solicitações pendentes: {len(pending_requests)}")
            for request in pending_requests:
                req_data = request.to_dict()
                req_id = request.id
                req_user_uid = req_data.get('user_uid', 'N/A')
                req_username = req_data.get('username', 'N/A')
                req_email = req_data.get('user_email_contato', 'N/A')  # Pega o email de contato
                req_message = req_data.get('mensagem', '(Sem mensagem)')
                req_time = req_data.get('timestamp')
                req_time_str = req_time.strftime('%d/%m/%Y %H:%M') if isinstance(req_time,
                                                                                 datetime) else "Data inválida"

                with st.expander(f"De: {req_username} ({req_email}) - Em: {req_time_str}"):
                    st.write(f"**UID do Usuário:** `{req_user_uid}`")
                    st.write("**Mensagem:**")
                    st.write(f"> {req_message}")
                    st.markdown("---")

                    col1_req, col2_req = st.columns(2)
                    with col1_req:
                        # Botão para marcar como processada
                        if st.button("Marcar como Processada", key=f"process_{req_id}", use_container_width=True):
                            try:
                                db.collection('solicitacoes_vip').document(req_id).update({'status': 'processado'})
                                st.success(f"Solicitação de {req_username} marcada como processada.")
                                st.rerun()  # Atualiza a lista
                            except Exception as e:
                                st.error(f"Erro ao atualizar status: {e}")

    except Exception as e:
        st.error(f"Erro ao buscar solicitações VIP: {e}")
    # --- FIM DA SEÇÃO VIP ---

    # --- Seção de Gerenciamento de Usuários ---
    st.markdown("---")
    st.subheader("👥 Gerenciar Usuários")
    try:
        users_stream = db.collection('usuarios').stream()
        users = list(users_stream)
    except Exception as e:
        st.error(f"Erro ao listar usuários: {e}")
        return

    st.write(f"Total usuários: {len(users)}")
    for u in users:
        d = u.to_dict()
        user_id = u.id
        nome = d.get('username', (d.get('dados_usuario') or {}).get('nome', '-'))
        # CORREÇÃO: Garante que current_role nunca seja None
        current_role = d.get('role') or 'free'

        st.markdown(f"**{nome}** (`{user_id}`)")
        st.write(f"Treinos: {len(d.get('frequencia', []))} | Role Atual: **{current_role.upper()}**")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("👁️ Ver Dados", key=f"ver_{user_id}"):
                st.json(d)

        with c2:
            if current_role != 'vip' and current_role != 'admin':
                if st.button("⭐ Tornar VIP", key=f"make_vip_{user_id}", type="primary"):
                    try:
                        db.collection('usuarios').document(user_id).update({'role': 'vip'})
                        st.success(f"{nome} agora é VIP!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao tornar VIP: {e}")

        with c3:
            if current_role != 'free' and current_role != 'admin':
                if st.button("⬇️ Tornar Free", key=f"make_free_{user_id}"):
                    try:
                        db.collection('usuarios').document(user_id).update({'role': 'free'})
                        st.success(f"{nome} agora é Free.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao tornar Free: {e}")

        with c4:
            if current_role != 'admin':
                if st.button("🗑️ Excluir", key=f"del_{user_id}"):
                    st.session_state['user_to_delete'] = user_id
                    st.session_state['confirm_delete_user'] = True
                    st.rerun()
        st.markdown("---")

    # Lógica de confirmação de exclusão
    if st.session_state.get('confirm_delete_user'):
        st.warning("Confirmar exclusão do usuário (irrevogável).")
        ca, cb = st.columns(2)
        with ca:
            if st.button("✅ Confirmar exclusão"):
                uid_del = st.session_state.get('user_to_delete')
                if uid_del:
                    try:
                        try:
                            auth.delete_user(uid_del)
                        except Exception:
                            pass
                        db.collection('usuarios').document(uid_del).delete()
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

def render_premade_workout_viewer():
    """Exibe o plano de treino pré-feito selecionado."""
    workout_id = st.session_state.get('selected_premade_workout')
    if not workout_id or workout_id not in PREMADE_WORKOUTS_DB:
        st.error("Erro ao carregar o treino. Voltando à biblioteca.")
        st.session_state.pop('selected_premade_workout', None)
        st.rerun()
        return

    workout = PREMADE_WORKOUTS_DB[workout_id]

    # Botão para voltar
    if st.button("← Voltar para a Biblioteca"):
        del st.session_state['selected_premade_workout']
        st.rerun()
        return

    st.title(workout["title"])
    st.markdown(f"_{workout['description']}_")
    st.markdown("---")

    # Reutiliza a lógica de exibição de 'render_meu_treino'
    plano = workout['plano']
    for nome_treino, exercicios_lista in plano.items():
        if not exercicios_lista:
            continue

        st.subheader(nome_treino)
        df_treino = pd.DataFrame(exercicios_lista)

        for index, row in df_treino.iterrows():
            exercicio = row.get('Exercício', 'N/A')
            series = row.get('Séries', 'N/A')
            repeticoes = row.get('Repetições', 'N/A')
            descanso = row.get('Descanso', 'N/A')

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
                    st.markdown("##### 📋 Instruções")
                    st.markdown(
                        f"- **Séries:** `{series}`\n- **Repetições:** `{repeticoes}`\n- **Descanso:** `{descanso}`")

                    ex_data = EXERCICIOS_DB.get(exercicio)
                    if ex_data:
                        st.markdown("---")
                        st.write(f"**Grupo Muscular:** {ex_data.get('grupo', 'N/A')}")
                        st.write(f"**Tipo:** {ex_data.get('tipo', 'N/A')}")
                        st.write(f"**Equipamento:** {ex_data.get('equipamento', 'N/A')}")
                        if ex_data.get('descricao'):
                            st.markdown("---")
                            st.markdown(f"**📝 Como Fazer:**\n{ex_data.get('descricao')}")
                    else:
                        st.warning(f"Exercício '{exercicio}' não encontrado na Base de Dados. Descrição indisponível.")
        st.markdown("---")


def render_workout_card_grid():
    """Exibe a grade de cards de treino pré-feitos."""
    st.info(
        "Explore programas de treino completos, criados por especialistas. Clique em 'Ver Plano de Treino' para ver os detalhes.")
    st.markdown("---")

    num_cols = 3
    workout_items = list(PREMADE_WORKOUTS_DB.items())

    for i in range(0, len(workout_items), num_cols):
        cols = st.columns(num_cols)
        batch = workout_items[i:i + num_cols]

        for j, (workout_id, workout) in enumerate(batch):
            with cols[j]:
                with st.container(border=True):
                    try:
                        st.image(workout["image_url"], use_container_width=True)
                    except Exception:
                        st.error("Imagem não pôde ser carregada.")

                    st.subheader(workout["title"])
                    st.caption(workout["description"])

                    if st.button("Ver Plano de Treino", key=workout_id, use_container_width=True, type="primary"):
                        st.session_state['selected_premade_workout'] = workout_id
                        st.rerun()


def render_vip_library():
    """Função principal da página 'Biblioteca VIP', decide o que mostrar."""
    st.title("📚 Biblioteca de Treinos VIP")

    # Verifica se o usuário tem acesso
    user_role = st.session_state.get('role', 'free')
    if user_role not in ['vip', 'admin']:
        st.error("🔒 Acesso restrito a usuários VIP")
        st.info("Faça upgrade para VIP para acessar esta biblioteca exclusiva!")
        if st.button("⭐ Tornar-se VIP"):
            st.session_state.selected_page = "Solicitar VIP"
            st.rerun()
        return

    # Verifica se um treino foi selecionado
    if st.session_state.get('selected_premade_workout'):
        # Se sim, mostra a visualização detalhada do treino
        render_premade_workout_viewer()
    else:
        # Se não, mostra a grade de cards para seleção
        render_workout_card_grid()

def render_nutricao_gated():
    user_role = st.session_state.get('role', 'free')

    # --- CORREÇÃO AQUI ---
    # Verifica se o role é 'vip' OU 'admin'
    if user_role in ['vip', 'admin']:
    # --- FIM DA CORREÇÃO ---
        render_nutricao_vip()
    else:
        render_nutricao_free()


# [NOVA] Página de "Anúncio" para usuários Free
def render_nutricao_free():
    st.title("🥗 Nutrição Avançada (VIP)")

    # Reutiliza a função de CTA (Call to Action) VIP
    render_vip_cta(
        title="✨ Desbloqueie sua Nutrição VIP!",
        text="A calculadora básica de TMB foi atualizada para um plano nutricional completo, exclusivo para membros VIP.",
        button_text="Quero o Plano Nutricional VIP!",
        key_prefix="cta_nutri"
    )

    st.markdown("---")
    st.subheader("O que você desbloqueia:")
    st.markdown("""
    * **Metas de Calorias e Macros de Precisão:** Baseado no seu nível de atividade e objetivo (cutting, bulking ou manutenção).
    * **Sugestão de Divisão de Refeições:** Um template de como dividir suas metas ao longo do dia.
    * **Biblioteca de Alimentos:** Exemplos de fontes limpas de proteínas, carboidratos e gorduras.
    * **Calculadora de Hidratação:** Saiba quanta água você realmente precisa beber.
    """)


# [NOVA] Página de Nutrição Robusta (apenas para VIPs)
def render_nutricao_vip():
    st.title("🥗 Plano de Nutrição VIP")
    dados = st.session_state.get('dados_usuario') or {}

    # Puxa dados do perfil ou usa defaults
    peso_default = float(dados.get('peso', 70.0))
    altura_default = float(dados.get('altura', 170.0))
    idade_default = int(dados.get('idade', 25))
    sexo_default_idx = 0 if dados.get('sexo', 'Masculino') == 'Masculino' else 1

    # ----------------- TABS DA PÁGINA -----------------
    tab_calc, tab_alimentos, tab_agua = st.tabs(["📊 Calculadora de Metas", "🥑 Biblioteca de Alimentos", "💧 Hidratação"])

    with tab_calc:
        st.subheader("1. Calcule suas Metas Diárias")
        st.caption("Baseado na fórmula de Mifflin-St Jeor e seus objetivos.")

        with st.form("form_nutri_vip"):
            col1, col2 = st.columns(2)
            with col1:
                peso = st.number_input("Peso (kg)", min_value=30.0, value=peso_default, step=0.1)
                altura = st.number_input("Altura (cm)", min_value=100.0, value=altura_default, step=0.1)
                idade = st.number_input("Idade", min_value=12, max_value=100, value=idade_default)

            with col2:
                sexo = st.selectbox("Sexo", ["Masculino", "Feminino"], index=sexo_default_idx)
                nivel_atividade = st.selectbox("Nível de Atividade Diária (incluindo treinos)",
                                               ['Sedentário (pouco/nenhum exercício)', 'Leve (1-3 dias/semana)',
                                                'Moderado (3-5 dias/semana)', 'Ativo (6-7 dias/semana)',
                                                'Muito Ativo (trabalho físico + treino)'], index=2)
                objetivo_dieta = st.selectbox("Qual seu objetivo nutricional?",
                                              ['Manter Peso (Manutenção)', 'Perder Peso Leve (Déficit de ~10%)',
                                               'Perder Peso (Déficit de ~20%)', 'Ganhar Peso Leve (Superávit de ~10%)',
                                               'Ganhar Peso (Superávit de ~20%)'], index=0)

            calc_submitted = st.form_submit_button("Calcular Metas Nutricionais")

        if calc_submitted:
            # 1. Calcular TMB
            tmb = calcular_tmb_mifflin(sexo, peso, altura, idade)
            # 2. Calcular Gasto Calórico Diário (TDEE)
            multiplicador = get_multiplicador_atividade(nivel_atividade)
            calorias_manutencao = tmb * multiplicador
            # 3. Ajustar pelo Objetivo
            calorias_meta = ajustar_calorias_objetivo(calorias_manutencao, objetivo_dieta)
            # 4. Calcular Macros VIP
            macros = calcular_macros_vip(calorias_meta, peso)

            st.session_state['macros_vip'] = macros  # Salva para usar na outra seção
            st.session_state['calorias_meta'] = calorias_meta

            st.success(f"Metas calculadas para o objetivo: **{objetivo_dieta}**")

            # Exibir resultados das metas
            kcal_col, prot_col, carb_col, gord_col = st.columns(4)
            kcal_col.metric("Calorias Totais", f"{calorias_meta:,.0f} kcal")
            prot_col.metric("Proteínas", f"{macros['proteina_g']:,.0f} g")
            carb_col.metric("Carboidratos", f"{macros['carboidratos_g']:,.0f} g")
            gord_col.metric("Gorduras", f"{macros['gordura_g']:,.0f} g")

        st.markdown("---")
        st.subheader("2. Sugestão de Divisão de Refeições")

        # Usa os dados salvos no session_state se existirem
        if 'macros_vip' in st.session_state:
            num_refeicoes = st.slider("Dividir em quantas refeições?", 3, 6, 4)
            df_refeicoes = distribuir_refeicoes(st.session_state['macros_vip'], num_refeicoes)
            st.dataframe(df_refeicoes, hide_index=True, use_container_width=True)
            st.caption(
                f"Esta é uma sugestão de divisão. O total diário é: {st.session_state['calorias_meta']:,.0f} kcal (P: {st.session_state['macros_vip']['proteina_g']}g, C: {st.session_state['macros_vip']['carboidratos_g']}g, G: {st.session_state['macros_vip']['gordura_g']}g)")
        else:
            st.info("Calcule suas metas acima para ver a sugestão de divisão de refeições.")

    with tab_alimentos:
        st.subheader("🥑 Biblioteca de Alimentos Sugeridos")
        st.caption("Use esta lista como inspiração para montar suas refeições com base nas metas calculadas.")

        col_p, col_c, col_g = st.columns(3)
        with col_p:
            st.markdown("<h5>🍗 Fontes de Proteína</h5>", unsafe_allow_html=True)
            st.markdown("\n".join(f"- {item}" for item in ALIMENTOS_DB["Proteínas"]))
        with col_c:
            st.markdown("<h5>🍚 Fontes de Carboidratos</h5>", unsafe_allow_html=True)
            st.markdown("\n".join(f"- {item}" for item in ALIMENTOS_DB["Carboidratos"]))
        with col_g:
            st.markdown("<h5>🥑 Fontes de Gordura</h5>", unsafe_allow_html=True)
            st.markdown("\n".join(f"- {item}" for item in ALIMENTOS_DB["Gorduras"]))

    with tab_agua:
        st.subheader("💧 Calculadora de Hidratação")
        peso_agua = st.number_input("Seu Peso (kg)", min_value=30.0, value=peso_default, step=0.1, key="peso_agua")
        ml_por_kg = st.slider("Mililitros (ml) por kg de peso", 30, 50, 35)

        meta_agua_l = (peso_agua * ml_por_kg) / 1000

        st.metric("Sua Meta Diária de Água", f"{meta_agua_l:.1f} Litros")
        st.caption("Lembre-se: em dias de treino intenso ou muito calor, você pode precisar de mais.")

# ---------------------------
# Page implementations
# ---------------------------
def render_workout_session():
    st.title("🔥 Treino em Andamento")

    # Pega os dados do estado da sessão
    plano_atual = st.session_state.get('current_workout_plan', [])
    idx_atual = st.session_state.get('current_exercise_index', 0)

    if not plano_atual or idx_atual >= len(plano_atual):
        st.error("Erro ao carregar o exercício atual. Voltando para a seleção de treino.")
        st.session_state['workout_in_progress'] = False
        time.sleep(2)
        st.rerun()
        return

    exercicio_atual = plano_atual[idx_atual]
    nome_exercicio = exercicio_atual.get('Exercício', 'Exercício Desconhecido')
    series_str = exercicio_atual.get('Séries', '3')
    try:
        num_series = int(str(series_str).split('-')[0])
    except ValueError:
        num_series = 3

    progresso = (idx_atual + 1) / len(plano_atual)
    col_prog, col_timer = st.columns(2)
    col_prog.progress(progresso, text=f"Exercício {idx_atual + 1} de {len(plano_atual)}")
    timer_placeholder = col_timer.empty()

    is_resting = False
    rest_timer_end_value = st.session_state.get('rest_timer_end', None)
    if rest_timer_end_value:
        remaining = rest_timer_end_value - time.time()
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

    with st.container(border=True):
        col_video, col_details = st.columns([1, 2])
        with col_video:
            video_url = find_exercise_video_youtube(nome_exercicio)
            if video_url:
                st.link_button("🎥 Assistir Execução", video_url)
                st.caption(f"Abre o vídeo de {nome_exercicio} no YouTube")
            else:
                st.info("Vídeo indisponível.")
        with col_details:
            st.header(nome_exercicio)
            st.markdown(
                f"**Séries:** `{exercicio_atual.get('Séries', 'N/A')}` | **Repetições:** `{exercicio_atual.get('Repetições', 'N/A')}`\n**Descanso:** `{exercicio_atual.get('Descanso', 'N/A')}`")
            ex_data = EXERCICIOS_DB.get(nome_exercicio, {})
            descricao_exercicio = ex_data.get('descricao')
            if descricao_exercicio:
                st.markdown("---")
                st.caption(f"📝 **Como Fazer:** {descricao_exercicio}")

    st.subheader("Registre suas séries")
    for i in range(num_series):
        set_key = f"set_{idx_atual}_{i}"
        if set_key not in st.session_state:
            st.session_state[set_key] = {'completed': False, 'weight': 0.0, 'reps': 0}
        set_info = st.session_state[set_key]
        cols = st.columns([1, 2, 2, 1])
        disable_inputs = is_resting and not set_info['completed']
        completed = cols[0].checkbox(f"Série {i + 1}", value=set_info['completed'], key=f"check_{set_key}",
                                     disabled=disable_inputs)

        if completed and not set_info['completed']:
            if is_resting:
                st.warning("Termine seu descanso!");
                st.session_state[set_key]['completed'] = False;
                st.rerun()
            else:
                set_info['completed'] = True
                descanso_str = exercicio_atual.get('Descanso', '60s')
                try:
                    rest_seconds = int(re.search(r'\d+', descanso_str).group())
                except:
                    rest_seconds = 60
                st.session_state.rest_timer_end = time.time() + rest_seconds
                st.session_state.workout_log.append(
                    {'data': date.today().isoformat(), 'exercicio': nome_exercicio, 'series': i + 1,
                     'peso': set_info['weight'], 'reps': set_info['reps'], 'timestamp': iso_now()})
                st.rerun()
        if not set_info['completed']:
            set_info['weight'] = cols[1].number_input("Peso (kg)", key=f"weight_{set_key}",
                                                      value=float(set_info.get('weight', 0.0)), format="%.1f",
                                                      disabled=disable_inputs)
            set_info['reps'] = cols[2].number_input("Reps", key=f"reps_{set_key}", value=int(set_info.get('reps', 0)),
                                                    disabled=disable_inputs)
        else:
            cols[1].write(f"Peso: **{set_info.get('weight', 0.0)} kg**")
            cols[2].write(f"Reps: **{set_info.get('reps', 0)}**")

    st.markdown("---")
    all_sets_done = all(
        st.session_state.get(f"set_{idx_atual}_{i}", {}).get('completed', False) for i in range(num_series))
    nav_cols = st.columns([1, 1, 1])

    with nav_cols[1]:  # Botão Central
        if all_sets_done:
            if idx_atual < len(plano_atual) - 1:
                if st.button("Próximo Exercício →", use_container_width=True, type="primary", disabled=is_resting):
                    st.session_state['current_exercise_index'] += 1
                    st.rerun()
            else:
                if st.button("✅ Concluir Último Exercício", use_container_width=True, type="primary",
                             disabled=is_resting):
                    hist = st.session_state.get('historico_treinos', [])
                    hist.extend(st.session_state.workout_log)
                    st.session_state['historico_treinos'] = hist
                    freq = st.session_state.get('frequencia', [])
                    today = date.today()
                    if today not in freq: freq.append(today); st.session_state['frequencia'] = freq
                    salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
                    st.session_state['workout_in_progress'] = False
                    st.session_state['workout_log'] = []
                    st.balloons()
                    st.success("Treino finalizado com sucesso!")

                    if st.session_state.get('role') == 'vip':
                        st.session_state['current_routine'] = COOLDOWN_ROUTINE_VIP_YOGA
                    else:
                        st.session_state['current_routine'] = COOLDOWN_ROUTINE

                    st.session_state.cooldown_in_progress = True
                    st.session_state.current_routine_exercise_index = 0

                    # --- CORREÇÃO AQUI ---
                    # Sintaxe de limpeza corrigida (usando loop for)
                    keys_to_delete = [k for k in st.session_state if k.startswith('set_')]
                    for k in keys_to_delete:
                        del st.session_state[k]
                    # --- FIM DA CORREÇÃO ---

                    time.sleep(1.5)
                    st.rerun()

    with nav_cols[2]:  # Botão da Direita
        if st.button("❌ Desistir do Treino", use_container_width=True):
            st.session_state['workout_in_progress'] = False
            st.session_state['workout_log'] = []
            st.session_state['rest_timer_end'] = None

            # --- CORREÇÃO AQUI ---
            # Sintaxe de limpeza corrigida (usando loop for)
            keys_to_delete = [k for k in st.session_state if k.startswith('set_')]
            for k in keys_to_delete:
                del st.session_state[k]
            # --- FIM DA CORREÇÃO ---

            st.warning("Treino cancelado.")
            time.sleep(1)
            st.rerun()

    # CTA para Cooldown VIP
    if all_sets_done and idx_atual == len(plano_atual) - 1 and st.session_state.get('role') == 'free':
        st.markdown("---")
        with st.container(border=True):
            st.info(
                "🧘 **Membros VIP têm acesso a rotinas de alongamento guiadas (Yoga, Foco em Recuperação) após o treino.**")
            if st.button("Quero as rotinas VIP!", key="cta_cooldown"):
                st.session_state['selected_page'] = "Solicitar VIP"
                st.rerun()


def render_warmup_session():
    st.title("🔥 Aquecimento Guiado")

    # [MODIFICADO] Pega a rotina selecionada do session_state
    # 'current_routine' é definida pelo botão clicado em 'render_meu_treino'
    # Se não for definida por algum motivo, usa a rotina padrão (WARMUP_ROUTINE)
    routine = st.session_state.get('current_routine', WARMUP_ROUTINE)

    idx = st.session_state.get('current_routine_exercise_index', 0)  # Pega o índice atual

    # Verifica se já terminou a rotina
    if idx >= len(routine):
        st.success("Aquecimento concluído! Pronto para o treino.")
        if st.button("Ir para Seleção de Treino", type="primary"):
            # Limpa estados da rotina ao sair
            st.session_state.warmup_in_progress = False
            st.session_state.current_routine_exercise_index = 0
            st.session_state.pop('current_routine', None)  # Limpa a rotina selecionada
            # Limpa estados do timer (caso tenham sido usados em versões anteriores)
            st.session_state.pop('routine_timer_end', None)
            st.session_state.pop('timer_finished_flag', None)
            st.rerun()
        st.stop()  # Interrompe a execução aqui se terminou

    # Pega os detalhes do exercício atual
    exercise = routine[idx]
    nome = exercise["nome"]
    # duracao = exercise["duracao_s"] # Duração não é mais usada ativamente
    descricao = exercise["descricao"]

    st.header(f"{idx + 1}/{len(routine)}. {nome}")
    st.progress((idx + 1) / len(routine))

    col_video, col_info = st.columns([1, 1])

    # --- Coluna do Vídeo ---
    with col_video:
        video_url = find_exercise_video_youtube(nome)
        if video_url:
            st.link_button("🎥 Assistir Execução", video_url)
            st.caption(f"Abre o vídeo de {nome} no YouTube")
        else:
            st.info("Vídeo indisponível.")

    # --- Coluna de Informações e Botão Próximo ---
    with col_info:
        st.markdown(f"**📝 Como Fazer:** {descricao}")
        st.markdown("---")

        # Botão para avançar para o próximo exercício
        if st.button("Próximo Exercício →", key=f"next_warmup_{idx}", type="primary"):
            st.session_state.current_routine_exercise_index += 1
            st.rerun()  # Recarrega para mostrar o próximo item

    # --- Fim da Coluna de Informações ---

    st.markdown("---")
    # Botão para Sair (sempre visível)
    if st.button("❌ Sair do Aquecimento", key="skip_warmup"):
        st.session_state.warmup_in_progress = False
        st.session_state.current_routine_exercise_index = 0
        st.session_state.pop('current_routine', None)  # Limpa a rotina selecionada
        st.session_state.pop('routine_timer_end', None)
        st.session_state.pop('timer_finished_flag', None)
        st.warning("Aquecimento interrompido.")
        time.sleep(1)
        st.rerun()

def render_cooldown_session():
    st.title("🧘 Alongamento Pós-Treino")

    routine = COOLDOWN_ROUTINE
    idx = st.session_state.current_routine_exercise_index

    if idx >= len(routine):
        st.success("Alongamento concluído! Ótima recuperação.")
        if st.button("Voltar ao Dashboard"):
            st.session_state.cooldown_in_progress = False
            # Poderia redirecionar para uma página específica se quisesse
            st.rerun()
        st.stop()

    exercise = routine[idx]
    nome = exercise["nome"]
    duracao = exercise["duracao_s"]  # Duração por lado, se aplicável
    descricao = exercise["descricao"]

    st.header(f"{idx + 1}. {nome}")
    st.progress((idx + 1) / len(routine))
    st.info(f"Mantenha a posição por aproximadamente **{duracao} segundos** (por lado, se aplicável).")

    col_video, col_info = st.columns([1, 1])

    with col_video:
        video_url = find_exercise_video_youtube(nome)
        if video_url:
            st.video(video_url)
        else:
            st.info("Vídeo indisponível.")

    with col_info:
        st.markdown(f"**📝 Como Fazer:** {descricao}")
        st.markdown("---")
        # Botão simples para avançar (sem timer obrigatório no cooldown)
        if st.button("Próximo Alongamento →", key=f"next_cooldown_{idx}", type="primary"):
            st.session_state.current_routine_exercise_index += 1
            st.rerun()

    if st.button("❌ Finalizar Alongamento Agora", key="skip_cooldown"):
        st.session_state.cooldown_in_progress = False
        st.warning("Alongamento finalizado.")
        time.sleep(1)
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
    # ========== VERIFICAÇÃO INICIAL ==========
    if not st.session_state.get('usuario_logado'):
        st.error("Usuário não logado.")
        return

    # ========== CONFIGURAÇÃO DO TEMA ==========
    theme = st.session_state['settings'].get('theme', 'light')
    if theme == 'dark':
        st.markdown("""
        <style>
        .main { background-color: #0E1117; }
        </style>
        """, unsafe_allow_html=True)

    # ========== HEADER DO DASHBOARD ==========
    st.title(f"🏠 Dashboard - {st.session_state.get('usuario_logado', 'Usuário')}")

    # Mostrar role se for VIP/Admin
    user_role = st.session_state.get('role', 'free')
    if user_role in ['vip', 'admin']:
        st.success(f"⭐ Status: {user_role.upper()}")

    # ========== FUNÇÃO DE CALLBACK PARA NAVEGAÇÃO ==========
    def navigate_to_page(page_name):
        st.session_state.selected_page = page_name
        st.rerun()

    # ========== SEÇÃO DE BEM-ESTAR DO DIA ==========
    st.markdown("---")
    st.subheader("📊 Resumo do Seu Dia")

    # Verificar treino do dia
    treino_hoje = verificar_treino_do_dia()
    col1, col2, col3 = st.columns(3)

    with col1:
        if treino_hoje:
            if treino_hoje == "Descanso":
                st.info("🎉 **Dia de Descanso**")
                st.caption("Aproveite para se recuperar!")
            else:
                st.success(f"🏋️ **Treino de Hoje**")
                st.write(f"**{treino_hoje}**")
                if st.button("💪 Iniciar Treino", key="iniciar_treino_dash", use_container_width=True):
                    navigate_to_page("Meu Treino")
        else:
            st.warning("📅 **Sem Planejamento**")
            st.caption("Configure seu planejamento semanal")
            if st.button("⚙️ Configurar", key="config_planejamento", use_container_width=True):
                navigate_to_page("Planejamento Semanal")

    with col2:
        # Estatística de treinos na semana
        hoje = datetime.now().date()
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        treinos_esta_semana = [
            d for d in st.session_state.get('frequencia', [])
            if isinstance(d, date) and d >= inicio_semana
        ]
        st.metric("Treinos Esta Semana", f"{len(treinos_esta_semana)}/7")

    with col3:
        # Próxima meta
        metas = st.session_state.get('metas', [])
        metas_ativas = [m for m in metas if m.get('status') != 'concluída']
        if metas_ativas:
            prox_meta = metas_ativas[0]
            st.metric("Próxima Meta", prox_meta.get('descricao', 'Meta'))
        else:
            st.metric("Metas", "Nenhuma ativa")

    # ========== NOTIFICAÇÕES ==========
    notificacoes = st.session_state.get('notificacoes', [])
    if notificacoes:
        st.markdown("---")
        st.subheader("🔔 Notificações")

        for notif in notificacoes[:3]:  # Mostra apenas as 3 mais recentes
            tipo = notif.get('tipo', 'info')
            msg = notif.get('msg', '')

            if tipo == 'lembrete_treino':
                st.success(f"🎯 {msg}")
            elif tipo == 'meta':
                st.warning(f"⏰ {msg}")
            elif tipo == 'nova_fase':
                st.info(f"🔄 {msg}")
            elif tipo == 'conquista':
                st.balloons()
                st.success(f"🎉 {msg}")
            else:
                st.info(f"ℹ️ {msg}")

        if len(notificacoes) > 3:
            with st.expander(f"Ver todas as {len(notificacoes)} notificações"):
                for notif in notificacoes[3:]:
                    st.write(f"• {notif.get('msg', '')}")

    # ========== ESTATÍSTICAS DE PROGRESSO ==========
    st.markdown("---")
    st.subheader("📈 Estatísticas de Progresso")

    col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)

    with col_stat1:
        total_treinos = len(st.session_state.get('frequencia', []))
        st.metric("Total de Treinos", total_treinos)

    with col_stat2:
        streak_atual = calcular_streak(st.session_state.get('frequencia', []))
        st.metric("Sequência Atual", f"{streak_atual} dias")

    with col_stat3:
        peso_atual = None
        historico_peso = st.session_state.get('historico_peso', [])
        if historico_peso:
            # Pega o último registro de peso
            ultimo_registro = historico_peso[-1]
            if isinstance(ultimo_registro, dict):
                peso_atual = ultimo_registro.get('peso')
            else:
                peso_atual = ultimo_registro
        st.metric("Peso Atual", f"{peso_atual} kg" if peso_atual else "N/A")

    with col_stat4:
        # Info de periodização
        num_treinos = len(set(st.session_state.get('frequencia', [])))
        if num_treinos > 0:
            info_periodizacao = verificar_periodizacao(num_treinos)
            st.metric("Fase Atual", info_periodizacao['fase_atual']['nome'])
        else:
            st.metric("Fase Atual", "Inicial")

    # ========== GRÁFICO DE FREQUÊNCIA ==========
    st.markdown("---")
    st.subheader("📊 Frequência de Treinos")

    frequencia = st.session_state.get('frequencia', [])
    if frequencia:
        # Converter para datas se necessário
        datas_treino = []
        for data in frequencia:
            if isinstance(data, datetime):
                datas_treino.append(data.date())
            elif isinstance(data, date):
                datas_treino.append(data)
            elif isinstance(data, str):
                try:
                    datas_treino.append(date.fromisoformat(data))
                except:
                    pass

        if datas_treino:
            # Criar DataFrame para o gráfico
            df_freq = pd.DataFrame({'data': datas_treino})
            df_freq['data'] = pd.to_datetime(df_freq['data'])
            df_freq['count'] = 1

            # Agrupar por mês
            df_mensal = df_freq.set_index('data').resample('M').count()

            fig = px.bar(
                df_mensal,
                x=df_mensal.index,
                y='count',
                title="Treinos por Mês",
                labels={'count': 'Treinos', 'data': 'Mês'}
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("📝 Ainda não há registros de treinos. Comece registrando seu primeiro treino!")
        if st.button("🎯 Registrar Primeiro Treino", key="primeiro_treino"):
            navigate_to_page("Registrar Treino")

    # ========== PLANO DE TREINO ATUAL ==========
    st.markdown("---")
    st.subheader("🏋️ Seu Plano de Treino")

    plano_treino = st.session_state.get('plano_treino')
    if plano_treino and verificar_plano_valido(plano_treino):
        st.success("✅ Plano de treino ativo")

        # Mostrar dias do plano
        dias_plano = list(plano_treino.keys())
        st.write(f"**Dias configurados:** {len(dias_plano)}")

        for i, dia_treino in enumerate(dias_plano[:3]):  # Mostra apenas os 3 primeiros
            exercicios = plano_treino[dia_treino]
            num_exercicios = len(exercicios) if isinstance(exercicios, (list, pd.DataFrame)) else 0
            st.write(f"• **{dia_treino}**: {num_exercicios} exercícios")

        if len(dias_plano) > 3:
            with st.expander(f"Ver todos os {len(dias_plano)} dias"):
                for dia_treino in dias_plano:
                    exercicios = plano_treino[dia_treino]
                    num_exercicios = len(exercicios) if isinstance(exercicios, (list, pd.DataFrame)) else 0
                    st.write(f"• **{dia_treino}**: {num_exercicios} exercícios")

        col_plano1, col_plano2 = st.columns(2)
        with col_plano1:
            if st.button("👀 Ver Plano Completo", key="ver_plano_dash", use_container_width=True):
                navigate_to_page("Meu Treino")

        with col_plano2:
            if st.button("🔄 Gerar Novo Plano", key="gerar_plano_dash", use_container_width=True):
                navigate_to_page("Questionário")

    else:
        st.warning("📝 Você ainda não tem um plano de treino gerado!")
        st.info("Complete o questionário para gerar seu plano personalizado.")
        if st.button("📋 Responder Questionário", key="questionario_dash", use_container_width=True, type="primary"):
            navigate_to_page("Questionário")

    # ========== METAS EM DESTAQUE ==========
    st.markdown("---")
    st.subheader("🎯 Metas em Andamento")

    metas = st.session_state.get('metas', [])
    metas_ativas = [m for m in metas if m.get('status') != 'concluída']

    if metas_ativas:
        for meta in metas_ativas[:2]:  # Mostra apenas 2 metas
            descricao = meta.get('descricao', 'Meta sem descrição')
            prazo = meta.get('prazo')
            tipo = meta.get('tipo', 'geral')

            col_meta1, col_meta2 = st.columns([3, 1])
            with col_meta1:
                st.write(f"**{descricao}**")
                if prazo:
                    try:
                        prazo_dt = date.fromisoformat(prazo) if isinstance(prazo, str) else prazo
                        dias_restantes = (prazo_dt - datetime.now().date()).days
                        if dias_restantes >= 0:
                            st.caption(f"⏳ {dias_restantes} dias restantes")
                        else:
                            st.caption("⚠️ Prazo expirado")
                    except:
                        st.caption("📅 Prazo não definido")

            with col_meta2:
                if st.button("📊", key=f"ver_meta_{hash(descricao)}"):
                    navigate_to_page("Metas")

        if len(metas_ativas) > 2:
            st.caption(f"E mais {len(metas_ativas) - 2} metas...")

        if st.button("👀 Ver Todas as Metas", key="ver_metas_dash", use_container_width=True):
            navigate_to_page("Metas")
    else:
        st.info("🎯 Você não tem metas ativas no momento.")
        if st.button("➕ Criar Primeira Meta", key="criar_meta_dash", use_container_width=True):
            navigate_to_page("Metas")

    # ========== AÇÕES RÁPIDAS ==========
    st.markdown("---")
    st.subheader("⚡ Ações Rápidas")

    col_rap1, col_rap2, col_rap3, col_rap4 = st.columns(4)

    with col_rap1:
        if st.button("📝 Registrar Treino", key="reg_treino_rapido", use_container_width=True):
            navigate_to_page("Registrar Treino")

    with col_rap2:
        if st.button("📸 Nova Foto", key="foto_rapido", use_container_width=True):
            navigate_to_page("Fotos")

    with col_rap3:
        if st.button("📊 Medidas", key="medidas_rapido", use_container_width=True):
            navigate_to_page("Medidas")

    with col_rap4:
        if st.button("👥 Rede Social", key="social_rapido", use_container_width=True):
            navigate_to_page("Rede Social")

    # ========== RECOMENDAÇÕES ==========
    st.markdown("---")
    st.subheader("💡 Recomendações")

    # Análise inteligente baseada nos dados do usuário
    total_treinos = len(st.session_state.get('frequencia', []))
    plano_valido = verificar_plano_valido(st.session_state.get('plano_treino'))
    tem_fotos = len(st.session_state.get('fotos_progresso', [])) > 0
    tem_medidas = len(st.session_state.get('medidas', [])) > 0

    recomendacoes = []

    if total_treinos == 0:
        recomendacoes.append("🎯 **Registre seu primeiro treino** para começar a acompanhar seu progresso!")

    if not plano_valido:
        recomendacoes.append("📋 **Complete o questionário** para gerar seu plano de treino personalizado.")

    if not tem_fotos:
        recomendacoes.append("📸 **Tire sua primeira foto de progresso** para visualizar suas mudanças.")

    if not tem_medidas:
        recomendacoes.append("📏 **Registre suas medidas** para acompanhar mudanças específicas.")

    if total_treinos > 0 and total_treinos % 10 == 0:
        recomendacoes.append(
            f"🎉 **Parabéns pelos {total_treinos} treinos!** Considere tirar novas fotos para comparar o progresso.")

    # Verificar se está há mais de 5 dias sem treinar
    if total_treinos > 0:
        datas_treino = []
        for data in st.session_state.get('frequencia', []):
            if isinstance(data, datetime):
                datas_treino.append(data.date())
            elif isinstance(data, date):
                datas_treino.append(data)
            elif isinstance(data, str):
                try:
                    datas_treino.append(date.fromisoformat(data))
                except:
                    pass

        if datas_treino:
            ultimo_treino = max(datas_treino)
            dias_sem_treinar = (datetime.now().date() - ultimo_treino).days

            if dias_sem_treinar > 5:
                recomendacoes.append(f"⏰ **Você está {dias_sem_treinar} dias sem treinar!** Que tal retomar hoje?")

    if recomendacoes:
        for rec in recomendacoes[:3]:  # Mostra apenas 3 recomendações
            st.info(rec)
    else:
        st.success("🌟 Você está no caminho certo! Continue com a consistência.")

# Função auxiliar para calcular streak (adicione esta função também)
def calcular_streak(frequencia):
    """Calcula a sequência atual de dias consecutivos de treino"""
    if not frequencia:
        return 0

    # Converter para datas
    datas_treino = []
    for data in frequencia:
        if isinstance(data, datetime):
            datas_treino.append(data.date())
        elif isinstance(data, date):
            datas_treino.append(data)
        elif isinstance(data, str):
            try:
                datas_treino.append(date.fromisoformat(data))
            except:
                pass

    if not datas_treino:
        return 0

    datas_treino = sorted(set(datas_treino))  # Remove duplicatas e ordena
    hoje = datetime.now().date()

    # Verifica se treinou hoje
    streak = 0
    if hoje in datas_treino or (hoje - timedelta(days=1)) in datas_treino:
        # Calcula streak retroativamente
        current_date = hoje
        while current_date in datas_treino:
            streak += 1
            current_date -= timedelta(days=1)

    return streak


# Função auxiliar para verificar treino do dia (adicione esta também)
def verificar_treino_do_dia():
    """Verifica qual é o treino programado para hoje"""
    planejamento = st.session_state.get('dados_usuario', {}).get('planejamento_semanal', {})
    if not planejamento:
        return None

    DIAS_SEMANA = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    dia_atual = datetime.now().weekday()
    nome_dia_atual = DIAS_SEMANA[dia_atual]

    return planejamento.get(nome_dia_atual, "Descanso")


def render_questionario():
    st.title("📋 Questionário de Perfil")

    # ==================== CORREÇÃO AQUI ====================
    # Chave estática para o formulário
    form_key = "f_questionario"
    # ========================================================

    with st.form(form_key):
        st.subheader("Informações Pessoais")

        col1, col2 = st.columns(2)
        with col1:
            # ==================== CORREÇÃO AQUI ====================
            # Chaves estáticas para cada widget
            nome = st.text_input("Nome Completo*", key="q_nome")
            idade = st.number_input("Idade*", min_value=12, max_value=100, key="q_idade")
            altura = st.number_input("Altura (cm)*", min_value=100, max_value=250, key="q_altura")

        with col2:
            peso = st.number_input("Peso (kg)*", min_value=30.0, max_value=200.0, step=0.1,
                                   key="q_peso")
            sexo = st.selectbox("Sexo*", ["Masculino", "Feminino"], key="q_sexo")

        st.subheader("Experiência e Objetivos")

        col3, col4 = st.columns(2)
        with col3:
            nivel = st.selectbox("Nível de Experiência*",
                                 ["Iniciante", "Intermediário/Avançado"],
                                 key="q_nivel")
            dias_semana = st.slider("Dias disponíveis por semana*", 1, 7, 3, key="q_dias_semana")

        with col4:
            objetivo = st.selectbox("Objetivo Principal*",
                                    ["Hipertrofia", "Emagrecimento", "Força", "Condicionamento"],
                                    key="q_objetivo")

        st.subheader("Restrições de Saúde")
        restricoes = st.multiselect(
            "Possui alguma restrição ou lesão?",
            ["Joelhos", "Lombar", "Ombros", "Cotovelos", "Punhos", "Tornozelos", "Nenhuma"],
            key="q_restricoes"
        )

        equipamentos = st.multiselect(
            "Equipamentos disponíveis",
            ["Barra", "Halteres", "Máquinas", "Polia", "Peso Corporal", "Elásticos", "Banco"],
            key="q_equipamentos"
        )

        observacoes = st.text_area("Observações adicionais", key="q_obs")

        if st.form_submit_button("🎯 Gerar Plano Personalizado", key="q_btn_submit"):
        # ========================================================
            # Validar campos obrigatórios
            if not all([nome, idade, altura, peso]):
                st.error("Por favor, preencha todos os campos obrigatórios (*)")
            else:
                # Salvar dados do usuário
                dados_usuario = {
                    'nome': nome,
                    'idade': idade,
                    'altura': altura,
                    'peso': peso,
                    'sexo': sexo,
                    'nivel': nivel,
                    'dias_semana': dias_semana,
                    'objetivo': objetivo,
                    'restricoes': restricoes,
                    'equipamentos': equipamentos,
                    'observacoes': observacoes
                }

                st.session_state['dados_usuario'] = dados_usuario

                # Gerar plano personalizado
                with st.spinner("Gerando seu plano personalizado..."):
                    plano_treino = gerar_plano_personalizado(dados_usuario)
                    st.session_state['plano_treino'] = plano_treino

                # Salvar no Firebase
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)

                st.success("🎉 Plano gerado com sucesso!")
                st.info("Acesse a página 'Meu Treino' para ver seu plano personalizado.")

                # ==================== CORREÇÃO AQUI ====================
                if st.button("👀 Ver Meu Treino", key="q_btn_ver_treino"):
                # ========================================================
                    st.session_state.selected_page = "Meu Treino"
                    st.rerun()


def render_meu_treino():
    st.title("💪 Meu Treino Personalizado")

    if not st.session_state.get('plano_treino'):
        st.warning("📝 Você ainda não tem um plano de treino gerado.")
        st.info("Vá para a página 'Questionário' para gerar seu plano personalizado!")

        if st.button("📋 Ir para o Questionário"):
            st.session_state.selected_page = "Questionário"
            st.rerun()
        return

    plano = st.session_state['plano_treino']

    if not isinstance(plano, dict):
        st.error("❌ Erro: Formato inválido do plano de treino.")
        return

    # Mostrar estatísticas do plano (código otimizado)
    total_exercicios = 0
    dias_validos = []

    for nome_treino, treino_data in plano.items():
        if treino_data is not None:
            if isinstance(treino_data, pd.DataFrame):
                if not treino_data.empty and 'Exercício' in treino_data.columns:
                    total_exercicios += len(treino_data)
                    dias_validos.append(nome_treino)
            elif isinstance(treino_data, list):
                if len(treino_data) > 0 and all(isinstance(item, dict) for item in treino_data):
                    total_exercicios += len(treino_data)
                    dias_validos.append(nome_treino)

    if not dias_validos:
        st.error("❌ Nenhum treino válido encontrado no plano.")
        return

    st.success(
        f"📊 Seu plano tem **{len(dias_validos)} dias** de treino com **{total_exercicios} exercícios** no total.")

    # Botões de ação - AGORA FUNCIONANDO CORRETAMENTE
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Regenerar Plano", type="secondary", use_container_width=True):
            if st.session_state.get('dados_usuario'):
                novo_plano = gerar_plano_personalizado(st.session_state['dados_usuario'], force_new=True)
                if novo_plano:
                    st.session_state['plano_treino'] = novo_plano
                    uid = st.session_state.get('user_uid')
                    if uid:
                        salvar_dados_usuario_firebase(uid)
                    st.success("Plano regenerado com sucesso!")
                    st.rerun()
            else:
                st.error("Não foi possível regenerar o plano. Dados do usuário não encontrados.")

    with col2:
        user_role = st.session_state.get('role', 'free')
        if user_role in ['vip', 'admin']:
            if st.button("📚 Biblioteca VIP", type="primary", use_container_width=True):
                st.session_state.selected_page = "Biblioteca VIP"
                st.rerun()
        else:
            if st.button("⭐ Desbloquear Biblioteca VIP", type="primary", use_container_width=True):
                st.session_state.selected_page = "Solicitar VIP"
                st.rerun()

    st.markdown("---")

    # Mostrar cada dia de treino (apenas os dias válidos)
    for nome_treino in dias_validos:
        treino_data = plano[nome_treino]

        # CORREÇÃO: Substituir a verificação booleana problemática
        if treino_data is None:
            continue

        # Verificação específica para DataFrame vazio
        if isinstance(treino_data, pd.DataFrame):
            if treino_data.empty:
                continue
        # Verificação específica para lista vazia
        elif isinstance(treino_data, list):
            if len(treino_data) == 0:
                continue

        # Converter para DataFrame se for lista
        if isinstance(treino_data, list):
            df_treino = pd.DataFrame(treino_data)
        else:
            df_treino = treino_data

        # CORREÇÃO: Verificar se o DataFrame resultante não está vazio
        if df_treino.empty or 'Exercício' not in df_treino.columns:
            continue

        col_header, col_action = st.columns([3, 1])

        with col_header:
            st.subheader(nome_treino)
            st.caption(f"{len(df_treino)} exercícios")

        with col_action:
            hoje = date.today()
            frequencia = st.session_state.get('frequencia', [])
            ja_treinou = hoje in frequencia

            if ja_treinou:
                st.success("✅ Treinado hoje")
            else:
                if st.button("🏁 Marcar como treinado", key=f"btn_{nome_treino}"):
                    if hoje not in frequencia:
                        frequencia.append(hoje)
                        st.session_state['frequencia'] = frequencia
                        salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
                        st.success(f"✅ {nome_treino} marcado como treinado!")
                        st.rerun()

        # Mostrar exercícios
        for index, row in df_treino.iterrows():
            exercicio = row.get('Exercício', 'N/A')
            series = row.get('Séries', 'N/A')
            repeticoes = row.get('Repetições', 'N/A')
            descanso = row.get('Descanso', 'N/A')

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
                    st.markdown("##### 📋 Instruções")
                    st.markdown(
                        f"- **Séries:** `{series}`\n- **Repetições:** `{repeticoes}`\n- **Descanso:** `{descanso}`")

                    ex_data = EXERCICIOS_DB.get(exercicio)
                    if ex_data:
                        st.markdown("---")
                        st.write(f"**Grupo Muscular:** {ex_data.get('grupo', 'N/A')}")
                        st.write(f"**Tipo:** {ex_data.get('tipo', 'N/A')}")
                        st.write(f"**Equipamento:** {ex_data.get('equipamento', 'N/A')}")
                        if ex_data.get('descricao'):
                            st.markdown("---")
                            st.markdown(f"**📝 Como Fazer:**\n{ex_data.get('descricao')}")
                    else:
                        st.warning(f"Exercício '{exercicio}' não encontrado na base de dados.")

        st.markdown("---")


def render_registrar_treino():
    st.title("📝 Registrar Treino")

    form_key = "f_registrar"  # <-- CHAVE ESTÁTICA

    with st.form(form_key):  # CHAVE ÚNICA
        col1, col2 = st.columns(2)

        with col1:
            data_treino = st.date_input("Data do Treino", value=datetime.now().date())
            tipo_treino = st.selectbox("Tipo de Treino",
                                       list(st.session_state.get('plano_treino', {}).keys()) if st.session_state.get(
                                           'plano_treino') else ["Treino Personalizado"])

        with col2:
            duracao = st.number_input("Duração (minutos)", min_value=1, max_value=300, value=60)
            intensidade = st.select_slider("Intensidade", options=["Leve", "Moderada", "Intensa", "Muito Intensa"],
                                           value="Moderada")

        observacoes = st.text_area("Observações (opcional)")

        if st.form_submit_button("💾 Registrar Treino"):
            # Validar dados
            if not data_treino:
                st.error("Selecione uma data para o treino.")
            else:
                # Registrar no histórico
                novo_treino = {
                    'data': data_treino.isoformat(),
                    'tipo': tipo_treino,
                    'duracao': duracao,
                    'intensidade': intensidade,
                    'observacoes': observacoes,
                    'timestamp': iso_now()
                }

                # Adicionar à frequência
                if data_treino not in st.session_state.get('frequencia', []):
                    st.session_state.setdefault('frequencia', []).append(data_treino)

                # Adicionar ao histórico
                st.session_state.setdefault('historico_treinos', []).append(novo_treino)

                # Salvar no Firebase
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)

                st.success("✅ Treino registrado com sucesso!")

                # ==========================================================
                # =        MODIFICAÇÃO: CHAMAR LÓGICA DE GAMIFICAÇÃO       =
                # ==========================================================
                if uid:
                    # 1. Calcular e salvar XP
                    xp_ganho = calcular_xp_ganho(novo_treino)
                    username = st.session_state.get('usuario_logado', 'Usuário Anônimo')
                    atualizar_xp_usuario(uid, username, xp_ganho)

                    # 2. Verificar novas conquistas
                    # (Pequeno delay para garantir que o save anterior foi processado)
                    time.sleep(0.5)
                    verificar_novas_conquistas(uid)
                # ==========================================================
                else:
                    st.balloons()  # Balões para o modo demo

                # Limpar formulário após sucesso
                st.rerun()

def render_prs(historico_completo):
    st.markdown("---")
    st.subheader("🏆 Recordes Pessoais (VIP)")
    if not historico_completo:
        st.info("Registre treinos para calcular seus recordes.")
        return
    df_hist = pd.DataFrame(historico_completo)
    if not all(col in df_hist.columns for col in ['exercicio', 'peso', 'reps', 'data']):
         st.warning("Dados históricos incompletos para PRs.")
         return
    df_hist['peso'] = pd.to_numeric(df_hist['peso'], errors='coerce')
    df_hist['reps'] = pd.to_numeric(df_hist['reps'], errors='coerce')
    try: # Tratamento robusto de datas
        def safe_to_date_prs(d):
            if isinstance(d, date): return d
            try: return datetime.fromisoformat(str(d).split('T')[0]).date()
            except: return None
        df_hist['data_obj'] = df_hist['data'].apply(safe_to_date_prs)
        df_hist = df_hist.dropna(subset=['peso', 'reps', 'data_obj'])
    except Exception: st.error("Erro ao processar datas para PRs."); return
    if df_hist.empty: st.info("Nenhum registro válido para PRs."); return

    exercicios_pr = [ # Lista de exercícios principais
        'Agachamento com Barra', 'Agachamento Goblet', 'Leg Press 45°', 'Supino Reto com Barra',
        'Supino Reto com Halteres', 'Desenvolvimento Militar com Barra', 'Desenvolvimento com Halteres (sentado)',
        'Remada Curvada com Barra', 'Puxada Alta (Lat Pulldown)', 'Barra Fixa', 'Levantamento Terra'
    ]
    df_prs = df_hist[df_hist['exercicio'].isin(exercicios_pr)].copy()
    if df_prs.empty: st.info("Nenhum registro para os exercícios principais de PR."); return

    # Pega o índice do maior peso para cada exercício
    prs_idx = df_prs.loc[df_prs.groupby('exercicio')['peso'].idxmax()].index
    prs = df_prs.loc[prs_idx].sort_values(by='exercicio')

    st.dataframe(
        prs[['exercicio', 'peso', 'reps', 'data_obj']],
        column_config={
            "exercicio": "Exercício",
            "peso": st.column_config.NumberColumn("Recorde (kg)", format="%.1f kg"),
            "reps": "Reps no Recorde",
            "data_obj": st.column_config.DateColumn("Data", format="DD/MM/YYYY")
        }, hide_index=True, use_container_width=True
    )


def render_solicitar_vip():
    st.title("✨ Solicitar Acesso VIP")
    st.markdown("""
    Desbloqueie o potencial máximo do FitPro! Usuários VIP têm acesso a:
    * 📈 Histórico de treino completo (sem limite de dias).
    * 🏆 Análise de Recordes Pessoais (PRs).
    * 🤸‍♂️ Rotinas de aquecimento e alongamento adicionais.
    * ... e muito mais em breve!
    """)

    # Adiciona um container com borda para deixar o formulário mais bonito
    with st.container(border=True):
        st.subheader("Formulário de Solicitação")
        st.info(
            "Preencha seu melhor e-mail para contato. Nossa equipe administrativa revisará sua solicitação e enviará as instruções de upgrade manualmente.")

        user_uid = st.session_state.get('user_uid')

        # Não vamos mais tentar buscar o email do Firebase Auth aqui
        # Em vez disso, vamos pedir ao usuário

        with st.form("form_solicitar_vip", clear_on_submit=True):

            # Mostra o nome do usuário, mas desabilitado (apenas para informação)
            st.text_input(
                "Usuário (para referência)",
                value=st.session_state.get('usuario_logado', 'N/A'),
                disabled=True
            )

            # [MUDANÇA] Caixa de texto para o email em vez de texto estático
            email_contato = st.text_input(
                "Seu melhor E-mail para contato*",
                placeholder="seu.email@exemplo.com"
            )

            mensagem = st.text_area(
                "Mensagem (Opcional)",
                placeholder="Gostaria de saber mais sobre o acesso VIP..."
            )

            submitted = st.form_submit_button("Enviar Solicitação VIP")

            if submitted:
                # [MUDANÇA] Validação do email inserido
                if not valid_email(email_contato):  # Reutiliza a função helper que já temos
                    st.error("Por favor, insira um e-mail válido.")
                elif user_uid and user_uid != 'demo-uid':
                    try:
                        # Salva a solicitação no Firestore com o email fornecido pelo usuário
                        db.collection('solicitacoes_vip').add({
                            'user_uid': user_uid,
                            'username': st.session_state.get('usuario_logado', 'N/A'),
                            'user_email_contato': email_contato,  # <-- Usa o email do formulário
                            'mensagem': mensagem,
                            'timestamp': firestore.SERVER_TIMESTAMP,
                            'status': 'pendente'
                        })
                        st.success("Solicitação enviada com sucesso! Entraremos em contato pelo e-mail fornecido.")
                        st.balloons()
                    except Exception as e:
                        st.error(f"Erro ao enviar solicitação: {e}")
                else:
                    st.warning("Função não disponível para modo demo ou usuário não identificado.")


def render_vip_cta(title="✨ Recurso VIP Exclusivo",
                   text="Esta funcionalidade está disponível apenas para membros VIP.",
                   button_text="Quero ser VIP!",
                   key_prefix="cta_vip"):
    """
    Renderiza um "anúncio" (Call to Action) padronizado para upgrade VIP.
    """
    with st.container(border=True):
        st.subheader(f"⭐ {title}")
        st.write(text)
        st.write("Desbloqueie este e outros recursos, como histórico ilimitado, análises avançadas e mais rotinas de treino!")

        # --- CORREÇÃO AQUI ---
        # A lógica de navegação foi movida para o 'on_click'
        # O Streamlit fará o rerun automaticamente após o on_click.
        st.button(
            button_text,
            key=f"{key_prefix}_{title.replace(' ', '')}",
            type="primary",
            on_click=navigate_to_page,  # Chama a função de callback
            args=("Solicitar VIP",)      # Passa o nome da página como argumento
        )
        # --- FIM DA CORREÇÃO ---


def render_progresso():
    st.title("📈 Progresso")
    historico_completo = st.session_state.get('historico_treinos', [])
    user_role = st.session_state.get('role', 'free')

    if not historico_completo:
        st.info("Registre treinos para ver gráficos.")
        return

    historico_filtrado = []
    # [GATING APLICADO AQUI]
    if user_role == 'free':
        limite_dias_prog = 60 # Exemplo: Free vê últimos 60 dias
        hoje_dt = datetime.now()
        data_limite_dt = hoje_dt - timedelta(days=limite_dias_prog)
        for record in historico_completo:
            try:
                data_str = record.get('data', '')
                if isinstance(data_str, date) and not isinstance(data_str, datetime): data_record_dt = datetime.combine(data_str, datetime.min.time())
                elif isinstance(data_str, datetime): data_record_dt = data_str
                else: data_record_dt = datetime.fromisoformat(str(data_str).split('T')[0])
                if data_record_dt >= data_limite_dt:
                    historico_filtrado.append(record)
            except: continue
        if len(historico_completo) > len(historico_filtrado):
            render_vip_cta(
                title="📊 Veja seu Histórico Completo",
                text=f"Usuários FREE têm acesso aos últimos {limite_dias_prog} dias. Membros VIP veem todo o histórico de progresso, sem limites!",
                button_text="Desbloquear Histórico Completo",
                key_prefix="cta_hist"
            )
            st.markdown("---")
    else: # VIP ou Admin
        historico_filtrado = historico_completo

    if not historico_filtrado:
         st.info("Nenhum treino registrado no período visível.")
         if user_role in ['vip', 'admin']: render_prs(historico_completo) # VIP/Admin ainda vê PRs
         return

    df = pd.DataFrame(historico_filtrado)
    try:
         def safe_to_datetime(d):
            if isinstance(d, datetime): return d
            if isinstance(d, date): return datetime.combine(d, datetime.min.time())
            try: return datetime.fromisoformat(str(d).split('T')[0])
            except: return pd.NaT
         df['data'] = df['data'].apply(safe_to_datetime); df = df.dropna(subset=['data'])
    except Exception as e: st.error(f"Erro ao processar datas do histórico: {e}"); return

    st.subheader("Volume Total de Treino por Dia")
    if not df.empty and 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce'); df = df.dropna(subset=['volume'])
        if not df.empty:
            vol = df.groupby(df['data'].dt.date)['volume'].sum().reset_index()
            fig = px.line(vol, x='data', y='volume', title='Volume por dia', markers=True)
            st.plotly_chart(fig, use_container_width=True)
            vol['rolling'] = vol['volume'].rolling(7, min_periods=1).mean()
            if len(vol['rolling']) >= 8:
                last, prev = vol['rolling'].iloc[-1], vol['rolling'].iloc[-8]
                if prev > 0 and abs(last - prev) / prev < 0.05:
                    st.warning("Possível platô detectado (variação de volume <5% nas últimas semanas).")
        else: st.info("Dados de volume insuficientes.")
    else: st.info("Dados de volume insuficientes.")

    # --- CORREÇÃO AQUI ---
    # Chama a função de PRs para VIPs e ADMINs
    if user_role in ['vip', 'admin']:
        render_prs(historico_completo)
    # --- FIM DA CORREÇÃO ---
    else:
        st.markdown("---")
        render_vip_cta(
            title="🏆 Análise de Recordes Pessoais (PRs)",
            text="Acompanhe seus recordes pessoais nos principais exercícios e veja sua força aumentar ao longo do tempo. Esta é uma análise exclusiva para membros VIP.",
            button_text="Desbloquear Análise de PRs",
            key_prefix="cta_prs"
        )


def render_fotos():
    st.title("📸 Fotos de Progresso")

    # ==================== CORREÇÃO AQUI ====================
    form_key = "f_fotos" # <-- CHAVE ESTÁTICA
    # ========================================================

    with st.form(form_key):
        st.subheader("📤 Nova Foto")

        col1, col2 = st.columns(2)
        with col1:
            # ==================== CORREÇÃO AQUI ====================
            # Chaves estáticas para cada widget
            data_foto = st.date_input("Data da Foto", value=datetime.now().date(), key="fotos_data")
            tipo_foto = st.selectbox("Ângulo",
                                     ["Frontal", "Lateral", "Posterior", "Outro"],
                                     key="fotos_tipo")

        with col2:
            peso_atual = st.number_input("Peso (kg)", min_value=30.0, max_value=200.0, step=0.1,
                                         key="fotos_peso")
            observacoes = st.text_area("Observações", key="fotos_obs")

        foto_upload = st.file_uploader("Escolha uma imagem", type=['png', 'jpg', 'jpeg'],
                                       key="fotos_upload")

        if st.form_submit_button("📸 Adicionar Foto", key="fotos_btn_add"):
        # ========================================================
            if not foto_upload:
                st.error("Selecione uma imagem para upload.")
            else:
                # Processar imagem
                try:
                    image = Image.open(foto_upload)
                    image_b64 = b64_from_pil(image)

                    nova_foto = {
                        'data': data_foto.isoformat(),
                        'tipo': tipo_foto,
                        'peso': peso_atual,
                        'observacoes': observacoes,
                        'imagem_b64': image_b64,
                        'timestamp': iso_now()
                    }

                    st.session_state.setdefault('fotos_progresso', []).append(nova_foto)

                    # Salvar no Firebase
                    uid = st.session_state.get('user_uid')
                    if uid:
                        salvar_dados_usuario_firebase(uid)

                    st.success("✅ Foto adicionada com sucesso!")
                    st.rerun()

                except Exception as e:
                    st.error(f"Erro ao processar imagem: {e}")

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
            st.session_state['medidas'] = medidas  # Atualiza sem ordenar aqui
            uid = st.session_state.get('user_uid')
            if uid: salvar_dados_usuario_firebase(uid)
            st.success(f"Medida de {tipo} ({valor} cm) salva!")
            st.rerun()

    st.markdown("---")

    # --- Exibição das Últimas Medidas Registradas ---
    st.subheader("Últimas Medidas Registradas")
    medidas_salvas = st.session_state.get('medidas', [])

    # ==================== CORREÇÃO AQUI ====================
    # A variável 'latest_measurements' deve ser inicializada aqui fora,
    # para que ela exista mesmo se 'medidas_salvas' estiver vazio.
    latest_measurements = {}
    # ========================================================

    if not medidas_salvas:
        st.info("Nenhuma medida registrada ainda. Use o formulário acima para adicionar.")
    else:
        # latest_measurements = {}  <-- REMOVIDO DE DENTRO DO ELSE
        try:
            df_medidas = pd.DataFrame(medidas_salvas)
            df_medidas['data'] = pd.to_datetime(df_medidas['data'])
            if 'timestamp' not in df_medidas.columns:
                df_medidas['timestamp'] = pd.to_datetime(df_medidas['data'], errors='coerce').fillna(
                    pd.Timestamp('1970-01-01'))
            else:
                df_medidas['timestamp'] = pd.to_datetime(df_medidas['timestamp'], errors='coerce').fillna(
                    pd.Timestamp('1970-01-01'))
            df_medidas_sorted = df_medidas.sort_values(by=['data', 'timestamp'], ascending=[False, False])
            df_latest = df_medidas_sorted.drop_duplicates(subset='tipo', keep='first')
            latest_measurements = df_latest.set_index('tipo').to_dict('index')
        except Exception as e:
            st.error(f"Erro ao processar as medidas salvas: {e}")

        tipos_esperados = ['Cintura', 'Quadril', 'Braço', 'Coxa', 'Peito']
        cols = st.columns(len(tipos_esperados))
        for i, tipo_m in enumerate(tipos_esperados):
            with cols[i]:
                if tipo_m in latest_measurements:
                    medida = latest_measurements[tipo_m];
                    valor_m = medida['valor'];
                    data_dt = medida['data']
                    data_m_str = data_dt.strftime('%d/%m/%Y') if pd.notnull(data_dt) else "Data inválida"
                    st.metric(label=f"{tipo_m}", value=f"{valor_m:.1f} cm", delta=f"Em {data_m_str}", delta_color="off")
                else:
                    st.metric(label=tipo_m, value="N/A", delta="Não registrado", delta_color="off")

    st.markdown("---")

    # --- [REFORMATADO] Exibição de Indicadores de Referência (Saúde) ---
    st.subheader("📊 Indicadores de Referência (Saúde)")  # Emoji adicionado
    dados_usuario = st.session_state.get('dados_usuario')

    if dados_usuario and 'altura' in dados_usuario and 'sexo' in dados_usuario:
        altura_cm = dados_usuario.get('altura', 0)
        sexo_usr = dados_usuario.get('sexo', 'Masculino')

        if altura_cm > 0:
            # 1. Relação Cintura-Altura (RCA)
            rca_ideal_max = altura_cm / 2
            st.markdown(f"🎯 **Relação Cintura-Altura (RCA):**")
            st.markdown(
                f"> Para **menor risco cardiovascular**, idealmente a circunferência da cintura deve ser **menor que `{rca_ideal_max:.1f} cm`** (metade da sua altura).")

            # 2. Circunferência Abdominal (Limites de Risco)
            st.markdown(f"⚠️ **Circunferência da Cintura (Risco Cardiovascular):**")
            if sexo_usr == 'Masculino':
                st.markdown("- Risco Aumentado: ≥ `94 cm`\n- Risco **Muito** Aumentado: ≥ `102 cm`")
            else:  # Feminino
                st.markdown("- Risco Aumentado: ≥ `80 cm`\n- Risco **Muito** Aumentado: ≥ `88 cm`")
            st.caption("Valores de referência comuns. Consulte um profissional de saúde.")

        else:
            st.warning("Altura não encontrada no seu perfil. Preencha o questionário para ver as referências.")

        # 3. Relação Cintura-Quadril (RCQ)
        cintura_recente = latest_measurements.get('Cintura', {}).get('valor')
        quadril_recente = latest_measurements.get('Quadril', {}).get('valor')
        if cintura_recente and quadril_recente and quadril_recente > 0:
            rcq = cintura_recente / quadril_recente
            st.markdown("---")  # Separador
            st.markdown(f"📉 **Relação Cintura-Quadril (RCQ) Atual:** `{rcq:.2f}`")
            if sexo_usr == 'Masculino':
                risco_rcq = "**Alto** 🔴" if rcq >= 0.90 else "**Baixo/Moderado** ✅"
                st.markdown(f"- Referência (Homens): Risco aumentado ≥ `0.90`. Seu risco atual: {risco_rcq}")
            else:  # Feminino
                risco_rcq = "**Alto** 🔴" if rcq >= 0.85 else "**Baixo/Moderado** ✅"
                st.markdown(f"- Referência (Mulheres): Risco aumentado ≥ `0.85`. Seu risco atual: {risco_rcq}")
            st.caption("RCQ é outro indicador de risco cardiovascular e distribuição de gordura.")

    else:
        st.info("ℹ️ Preencha o questionário (altura e sexo) para visualizar indicadores de referência.")


def render_planner():
    st.title("📅 Planejamento Semanal")

    if not st.session_state.get('plano_treino'):
        st.warning("Você precisa gerar um plano de treino primeiro no Questionário!")
        if st.button("Ir para Questionário", key="planner_btn_ir_questionario"):  # <-- CHAVE ESTÁTICA
            st.session_state.selected_page = "Questionário"
            st.rerun()
        return

    dados_usuario = st.session_state.get('dados_usuario', {})
    dias_semana = dados_usuario.get('dias_semana', 3)
    plano_treino = st.session_state.get('plano_treino', {})
    user_role = st.session_state.get('role', 'free')

    # Inicializa o planejamento semanal se não existir
    if 'planejamento_semanal' not in st.session_state:
        st.session_state.planejamento_semanal = {}

    # ========== SEÇÃO DE PLANEJAMENTO AUTOMÁTICO (APENAS VIP) ==========
    if user_role in ['vip', 'admin']:
        st.subheader("🤖 Planejamento Automático VIP")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.success("⭐ **Recurso Exclusivo VIP**")
            st.info("Gere automaticamente um cronograma semanal otimizado baseado no seu plano de treino.")

        with col2:
            if st.button("🎯 Gerar Planejamento Automático",
                         use_container_width=True,
                         type="primary",
                         key="planner_btn_auto"):  # <-- CHAVE ESTÁTICA
                with st.spinner("Gerando planejamento otimizado..."):
                    planejamento_auto = gerar_planejamento_automatico(dias_semana, plano_treino)
                    st.session_state.planejamento_semanal = planejamento_auto
                    st.success("Planejamento automático gerado com sucesso!")
                    st.rerun()

    else:
        # ========== SEÇÃO PARA USUÁRIOS FREE ==========
        st.subheader("🤖 Planejamento Automático")

        with st.container(border=True):
            st.warning("🔒 Recurso Exclusivo VIP")
            st.info("O planejamento automático inteligente está disponível apenas para usuários VIP.")
            st.markdown("""
            **Desbloqueie com o VIP:**
            - ✅ Geração automática de cronogramas
            - ✅ Distribuição inteligente de treinos  
            - ✅ Otimização baseada em seus objetivos
            - ✅ Estratégias avançadas (PPL, Upper/Lower, etc.)
            """)

            if st.button("⭐ Tornar-se VIP",
                         type="primary",
                         use_container_width=True,
                         key="planner_btn_tornar_vip"):  # <-- CHAVE ESTÁTICA
                st.session_state.selected_page = "Solicitar VIP"
                st.rerun()

    st.markdown("---")

    # ========== SEÇÃO DE AJUSTE MANUAL (PARA TODOS) ==========
    st.subheader("✏️ Ajuste Manual do Planejamento")
    st.caption("Disponível para todos os usuários - Configure manualmente sua semana de treinos:")

    DIAS_SEMANA = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    opcoes_treino = ["Descanso"] + list(plano_treino.keys())

    planejamento_atual = st.session_state.get('planejamento_semanal', {})

    # Garante que todos os dias estão no planejamento
    for dia in DIAS_SEMANA:
        if dia not in planejamento_atual:
            planejamento_atual[dia] = "Descanso"

    # Interface de edição manual

    # ==================== CORREÇÃO AQUI ====================
    # A chave do formulário também deve ser estática
    with st.form("f_planner_manual"):  # <-- CHAVE ESTÁTICA
        # ========================================================
        st.write("**Defina o treino para cada dia:**")

        colunas = st.columns(2)
        for i, dia in enumerate(DIAS_SEMANA):
            with colunas[i % 2]:
                treino_atual = planejamento_atual.get(dia, "Descanso")
                novo_treino = st.selectbox(
                    f"{dia}",
                    opcoes_treino,
                    index=opcoes_treino.index(treino_atual) if treino_atual in opcoes_treino else 0,
                    # Chave dinâmica, mas consistente baseada no nome do dia
                    key=f"planner_select_{dia}"
                )
                planejamento_atual[dia] = novo_treino

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.form_submit_button("💾 Salvar Planejamento Manual",
                                     key="planner_btn_salvar",  # <-- CHAVE ESTÁTICA
                                     use_container_width=True):
                st.session_state.planejamento_semanal = planejamento_atual
                st.success("Planejamento salvo com sucesso!")

        with col_btn2:
            if st.form_submit_button("🔄 Limpar Planejamento",
                                     key="planner_btn_limpar",  # <-- CHAVE ESTÁTICA
                                     use_container_width=True):
                st.session_state.planejamento_semanal = {}
                st.success("Planejamento limpo!")

    st.markdown("---")

    # ========== VISUALIZAÇÃO DO PLANEJAMENTO ==========
    st.subheader("👀 Visualização do Seu Planejamento Semanal")

    if not st.session_state.planejamento_semanal:
        if user_role in ['vip', 'admin']:
            st.info("💡 Use o gerador automático VIP ou configure manualmente acima.")
        else:
            st.info("💡 Configure manualmente seu planejamento semanal acima.")
    else:
        # Estatísticas do planejamento
        dias_treino = [dia for dia, treino in st.session_state.planejamento_semanal.items() if treino != "Descanso"]
        dias_descanso = [dia for dia, treino in st.session_state.planejamento_semanal.items() if treino == "Descanso"]

        col_stat1, col_stat2, col_stat3 = st.columns(3)

        # ==================== CORREÇÃO AQUI ====================
        # st.metric não aceita o argumento 'key'
        with col_stat1:
            st.metric("Dias de Treino", len(dias_treino))
        with col_stat2:
            st.metric("Dias de Descanso", len(dias_descanso))
        with col_stat3:
            st.metric("Treinos Diferentes", len(set(st.session_state.planejamento_semanal.values()) - {
                "Descanso"}))
        # ========================================================

        # Grade visual do planejamento
        st.write("**📊 Seu Cronograma Semanal:**")

        colunas_semana = st.columns(7)

        for i, dia in enumerate(DIAS_SEMANA):
            with colunas_semana[i]:
                treino = st.session_state.planejamento_semanal.get(dia, "Descanso")

                if treino == "Descanso":
                    st.error("😴 Descanso")
                    st.caption("Dia de recuperação")
                else:
                    st.success(f"🏋️ {treino}")
                    # Mostra quantos exercícios tem nesse treino
                    num_exercicios = len(plano_treino.get(treino, []))
                    st.caption(f"{num_exercicios} exercícios")

        # Botão para aplicar o planejamento
        st.markdown("---")
        if st.button("✅ Aplicar Este Planejamento",
                     type="primary",
                     use_container_width=True,
                     key="planner_btn_aplicar"):  # <-- CHAVE ESTÁTICA
            # Salva o planejamento nos dados do usuário
            if 'dados_usuario' not in st.session_state:
                st.session_state.dados_usuario = {}

            st.session_state.dados_usuario['planejamento_semanal'] = st.session_state.planejamento_semanal
            st.session_state.dados_usuario['dias_semana_list'] = [DIAS_SEMANA.index(dia) for dia in dias_treino]

            # Salva no Firebase
            uid = st.session_state.get('user_uid')
            if uid:
                salvar_dados_usuario_firebase(uid)

            st.success("🎉 Planejamento aplicado com sucesso! Você receberá lembretes nos dias de treino.")

    # ========== DICAS E INFORMAÇÕES ==========

    # ==================== CORREÇÃO AQUI ====================
    # st.expander não aceita o argumento 'key'
    with st.expander("💡 Dicas para um Bom Planejamento"):
        # ========================================================
        if user_role in ['vip', 'admin']:
            st.markdown("""
            **🌟 Dicas VIP:**
            - Use o **gerador automático** como base e ajuste conforme necessário
            - **Distribua grupos musculares**: Evite treinar o mesmo grupo em dias consecutivos
            - **Inclua descansos**: Músculos crescem durante o descanso
            - **Revise periodicamente**: Ajuste o planejamento a cada 4-6 semanas
            """)
        else:
            st.markdown("""
            **📋 Dicas para Planejamento Manual:**
            - **Distribua grupos musculares**: Evite treinar o mesmo grupo em dias consecutivos
            - **Inclua descansos**: Músculos crescem durante o descanso (recomendado: 1-2 dias/semana)
            - **Ouça seu corpo**: Ajuste conforme sua recuperação
            - **Mantenha consistência**: Seguir o cronograma é mais importante que a intensidade

            **💡 Exemplo de Distribuição:**
            - **2 dias/semana**: Superior + Inferior
            - **3 dias/semana**: Push + Pull + Legs ou ABC
            - **4 dias/semana**: Upper + Lower 2x
            - **5+ dias/semana**: PPL + Upper/Lower ou especialização
            """)

    # ========== UPSELL PARA VIP ==========
    if user_role not in ['vip', 'admin']:
        st.markdown("---")
        # ==================== CORREÇÃO AQUI ====================
        # st.container não aceita o argumento 'key'
        with st.container(border=True):
            # ========================================================
            st.subheader("🚀 Quer economizar tempo?")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown("""
                **Torne-se VIP e ganhe acesso ao:**
                - ✅ **Gerador automático** de planejamento
                - ✅ **Distribuição inteligente** baseada em seu perfil
                - ✅ **Estratégias avançadas** (PPL, Upper/Lower, etc.)
                - ✅ **Otimização automática** de descansos
                """)
            with col2:
                if st.button("⭐ Virar VIP",
                             use_container_width=True,
                             type="primary",
                             key="planner_btn_virar_vip_2"):  # <-- CHAVE ESTÁTICA (diferente da outra)
                    st.session_state.selected_page = "Solicitar VIP"
                    st.rerun()


def verificar_treino_do_dia():
    """Verifica qual é o treino programado para hoje"""
    planejamento = st.session_state.get('dados_usuario', {}).get('planejamento_semanal', {})
    if not planejamento:
        return None

    DIAS_SEMANA = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    dia_atual = datetime.now().weekday()
    nome_dia_atual = DIAS_SEMANA[dia_atual]

    return planejamento.get(nome_dia_atual, "Descanso")

def suggest_days(dias_sem: int):
    if dias_sem <= 0: return []
    step = 7 / dias_sem
    return sorted(list(set([int(round(i * step)) % 7 for i in range(dias_sem)])))


def render_metas():
    st.title("🎯 Metas e Objetivos")

    # ==================== CORREÇÃO AQUI ====================
    form_key = "f_metas" # <-- CHAVE ESTÁTICA
    # ========================================================

    with st.form(form_key):
        st.subheader("➕ Nova Meta")

        col1, col2 = st.columns(2)
        with col1:
            # ==================== CORREÇÃO AQUI ====================
            # Chaves estáticas para cada widget
            descricao = st.text_input("Descrição da Meta*", key="metas_desc")
            tipo = st.selectbox("Tipo de Meta",
                                ["Peso", "Medidas", "Força", "Consistência", "Outro"],
                                key="metas_tipo")

        with col2:
            valor_alvo = st.text_input("Valor Alvo (ex: 70kg, 100cm)", key="metas_valor")
            prazo = st.date_input("Prazo", min_value=date.today(), key="metas_prazo")

        if st.form_submit_button("🎯 Adicionar Meta", key="metas_btn_add"):
        # ========================================================
            if not descricao:
                st.error("Digite uma descrição para a meta.")
            else:
                nova_meta = {
                    'descricao': descricao,
                    'tipo': tipo,
                    'valor_alvo': valor_alvo,
                    'prazo': prazo.isoformat() if prazo else None,
                    'status': 'ativa',
                    'data_criacao': iso_now()
                }

                st.session_state.setdefault('metas', []).append(nova_meta)

                # Salvar no Firebase
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)

                st.success("✅ Meta adicionada com sucesso!")
                st.rerun()

    # Lista de metas existentes
    st.markdown("---")
    st.subheader("📋 Metas Ativas")

    metas = st.session_state.get('metas', [])
    if not metas:
        st.info("Você ainda não tem metas definidas.")
    else:
        for i, meta in enumerate(metas):
            with st.expander(f"🎯 {meta.get('descricao', 'Meta sem descrição')}"):
                col1, col2, col3 = st.columns([3, 1, 1])

                with col1:
                    st.write(f"**Tipo:** {meta.get('tipo', 'N/A')}")
                    if meta.get('valor_alvo'):
                        st.write(f"**Alvo:** {meta.get('valor_alvo')}")
                    if meta.get('prazo'):
                        prazo_dt = date.fromisoformat(meta['prazo']) if isinstance(meta['prazo'], str) else meta[
                            'prazo']
                        dias_restantes = (prazo_dt - date.today()).days
                        st.write(f"**Prazo:** {prazo_dt.strftime('%d/%m/%Y')} ({dias_restantes} dias)")

                with col2:
                    # ==================== CORREÇÃO AQUI ====================
                    # Chave dinâmica, mas consistente baseada no índice 'i'
                    if st.button("✅", key=f"metas_btn_concluir_{i}"):
                    # ========================================================
                        meta['status'] = 'concluída'
                        salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
                        st.rerun()

                with col3:
                    # ==================== CORREÇÃO AQUI ====================
                    if st.button("🗑️", key=f"metas_btn_excluir_{i}"):
                    # ========================================================
                        metas.pop(i)
                        salvar_dados_usuario_firebase(st.session_state.get('user_uid'))
                        st.rerun()


def calcular_tmb_mifflin(sexo, peso, altura, idade) -> float:
    """Calcula TMB (Taxa Metabólica Basal) usando a fórmula Mifflin-St Jeor."""
    if sexo.lower() == 'masculino':
        return (10 * peso) + (6.25 * altura) - (5 * idade) + 5
    else:  # Feminino
        return (10 * peso) + (6.25 * altura) - (5 * idade) - 161


def get_multiplicador_atividade(nivel_atividade_str: str) -> float:
    """Retorna o multiplicador TDEE com base no nível de atividade."""
    niveis = {
        'Sedentário (pouco/nenhum exercício)': 1.2,
        'Leve (1-3 dias/semana)': 1.375,
        'Moderado (3-5 dias/semana)': 1.55,
        'Ativo (6-7 dias/semana)': 1.725,
        'Muito Ativo (trabalho físico + treino)': 1.9
    }
    return niveis.get(nivel_atividade_str, 1.375)  # Default para 'Leve'


def ajustar_calorias_objetivo(calorias_base: float, objetivo_dieta: str) -> float:
    """Ajusta as calorias de manutenção com base no objetivo (cutting/bulking)."""
    ajustes = {
        'Perder Peso (Déficit de ~20%)': 0.8,
        'Perder Peso Leve (Déficit de ~10%)': 0.9,
        'Manter Peso (Manutenção)': 1.0,
        'Ganhar Peso Leve (Superávit de ~10%)': 1.1,
        'Ganhar Peso (Superávit de ~20%)': 1.2
    }
    return calorias_base * ajustes.get(objetivo_dieta, 1.0)  # Default para 'Manter'


def calcular_macros_vip(calorias_totais: float, peso_kg: float) -> dict:
    """Calcula a divisão de macros (Proteína, Gordura, Carboidrato)."""
    # Regra: 2.0g de proteína por kg de peso
    proteina_g = max(1.6 * peso_kg, peso_kg * 2.0)  # Mínimo de 1.6g/kg, alvo 2.0g/kg
    proteina_kcal = proteina_g * 4

    # Regra: 0.8g de gordura por kg de peso
    gordura_g = max(0.6 * peso_kg, peso_kg * 0.8)  # Mínimo 0.6g/kg, alvo 0.8g/kg
    gordura_kcal = gordura_g * 9

    # Restante das calorias vem dos carboidratos
    carboidratos_kcal = calorias_totais - proteina_kcal - gordura_kcal
    if carboidratos_kcal < 0:  # Caso de déficit calórico extremo
        carboidratos_kcal = 0
    carboidratos_g = carboidratos_kcal / 4

    return {'proteina_g': round(proteina_g), 'gordura_g': round(gordura_g), 'carboidratos_g': round(carboidratos_g)}


def distribuir_refeicoes(macros: dict, num_refeicoes: int) -> pd.DataFrame:
    """Gera uma tabela de sugestão de divisão de macros por refeição."""
    if num_refeicoes <= 0: return pd.DataFrame()

    p_por_refeicao = round(macros['proteina_g'] / num_refeicoes)
    g_por_refeicao = round(macros['gordura_g'] / num_refeicoes)
    c_por_refeicao = round(macros['carboidratos_g'] / num_refeicoes)
    kcal_por_refeicao = (p_por_refeicao * 4) + (g_por_refeicao * 9) + (c_por_refeicao * 4)

    refeicoes = []
    for i in range(1, num_refeicoes + 1):
        refeicoes.append({
            "Refeição": f"Refeição {i}",
            "Proteína (g)": p_por_refeicao,
            "Gordura (g)": g_por_refeicao,
            "Carboidratos (g)": c_por_refeicao,
            "Calorias (kcal)": kcal_por_refeicao
        })

    return pd.DataFrame(refeicoes)

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

# ---------------------------
# Run app
# ---------------------------
# [MODIFICADO] Função run() para verificar o token do Hugging Face
def run():
    # ========== ADICIONE ESTA LINHA NO INÍCIO ==========
    ensure_session_defaults()  # ← GARANTE QUE TUDO ESTÁ INICIALIZADO
    # ===================================================

    # Lógica de login
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


def main():
    """Função principal da aplicação"""
    ensure_session_defaults()

    # DEBUG: Verificar estado (pode remover depois)
    # st.write("DEBUG - usuario_logado:", st.session_state.get('usuario_logado'))
    # st.write("DEBUG - user_uid:", st.session_state.get('user_uid'))
    # st.write("DEBUG - cookie user_uid:", cookies.get('user_uid'))

    # Se não tem usuário logado na sessão, verificar cookies
    if not st.session_state.get('usuario_logado'):
        render_auth()  # ⬅️ AGORA ESTA FUNÇÃO VERIFICA COOKIES TAMBÉM
    else:
        render_main()


# EXECUTAR APENAS UMA VEZ
if __name__ == "__main__":
    main()




