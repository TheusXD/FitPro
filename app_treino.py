# app_fitpro.py
"""
FitPro - App completo pronto para deploy
- Spinner em operações de I/O (salvar/carregar)
- Confirmações elegantes (st.dialog() quando disponível, fallback)
- Cards visuais para treinos + gráfico por exercício
- Calendário visual de treinos
- Firebase (Auth + Firestore) via st.secrets["firebase_credentials"]
- Compatibilidade Streamlit (st.rerun fallback)
"""
import os
import re
import io
import json
import time
import base64
import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from PIL import Image, ImageChops, ImageFilter, ImageStat

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
# Ensure st.rerun exists (fallback to experimental_rerun if extremely old)
if not hasattr(st, "rerun") and hasattr(st, "experimental_rerun"):
    st.rerun = st.experimental_rerun  # type: ignore

# Provide a safe wrapper for st.dialog / st.modal: check availability
HAS_ST_DIALOG = hasattr(st, "dialog")
HAS_ST_MODAL = hasattr(st, "modal")

# ---------------------------
# Page config
# ---------------------------
st.set_page_config(page_title="FitPro", page_icon="🏋️", layout="wide")

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
    img1_s = img1.convert('L').resize((256,256))
    img2_s = img2.convert('L').resize((256,256))
    arr1 = np.array(img1_s).astype(float)
    arr2 = np.array(img2_s).astype(float)
    mse = float(((arr1 - arr2)**2).mean())
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
def init_firebase():
    try:
        creds = dict(st.secrets["firebase_credentials"])
        # fix newline escapes
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
        'settings': {'theme':'light','notify_on_login':True},
        'offline_mode': False,
        'confirm_excluir_foto': False,
        'foto_a_excluir': None,
    }
    for k,v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

ensure_session_defaults()

# ---------------------------
# Exercises DB (small)
# ---------------------------
EXERCICIOS_DB = {
    'Agachamento com Halteres': {'grupo':'Pernas', 'video':'https://youtu.be/example1', 'dicas':['Mantenha o core']},
    'Leg Press 45°': {'grupo':'Pernas', 'video':'https://youtu.be/example2', 'dicas':['Empurre com calcanhar']},
    'Supino Reto com Halteres': {'grupo':'Peito', 'video':'https://youtu.be/example3', 'dicas':['Escápulas retraídas']},
    'Remada Sentada (máquina)': {'grupo':'Costas', 'video':'https://youtu.be/example4', 'dicas':['Puxe com os cotovelos']},
    'Rosca Direta com Halteres': {'grupo':'Bíceps', 'video':'https://youtu.be/example5', 'dicas':['Controle a descida']},
}

# ---------------------------
# Plan serialization helpers
# ---------------------------
def plan_to_serial(plano: Optional[Dict[str, Any]]):
    if not plano:
        return None
    out = {}
    for k,v in plano.items():
        if isinstance(v, pd.DataFrame):
            out[k] = v.to_dict(orient='records')
        else:
            out[k] = v
    return out

def serial_to_plan(serial: Optional[Dict[str, Any]]):
    if not serial:
        return None
    out = {}
    for k,v in serial.items():
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
    if not uid:
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
            # small delay to show spinner
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
        st.success("✅ Dados carregados!")
    except Exception as e:
        st.error("Erro ao carregar do Firestore:")
        st.error(str(e))

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
            'email': email,
            'username': nome,
            'dados_usuario': {'nome': nome},
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
            'password_hash': sha256(senha),
            'data_criacao': datetime.now()
        })
        return True, "Usuário criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"

def verificar_credenciais_firebase(username_or_email: str, senha: str) -> (bool, str):
    # username_or_email may be email or demo
    if username_or_email == 'demo' and senha == 'demo123':
        st.session_state['user_uid'] = 'demo-uid'
        st.session_state['usuario_logado'] = 'Demo'
        # try to load demo doc
        doc = db.collection('usuarios').document('demo-uid').get()
        if doc.exists:
            carregar_dados_usuario_firebase('demo-uid')
        else:
            # fill demo data
            st.session_state['dados_usuario'] = {'nome':'Demo','peso':75,'altura':175,'nivel':'Iniciante','dias_semana':3}
            st.session_state['plano_treino'] = None
            st.session_state['frequencia'] = []
            st.session_state['historico_treinos'] = []
            st.session_state['metas'] = []
            st.session_state['fotos_progresso'] = []
        return True, "Modo demo ativado."
    try:
        # allow login by email only (we store password_hash)
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
            return True, f"Bem-vindo(a), {st.session_state['usuario_logado']}!"
        else:
            return False, "Senha incorreta."
    except auth.UserNotFoundError:
        return False, "Usuário não encontrado."
    except Exception as e:
        return False, f"Erro ao autenticar: {e}"

# ---------------------------
# Periodization
# ---------------------------
def verificar_periodizacao(num_treinos:int):
    TREINOS = 20
    ciclo = num_treinos // TREINOS
    fase_idx = ciclo % 3
    treinos_no_ciclo = num_treinos % TREINOS
    fases = [
        {'nome':'Hipertrofia','series':'3-4','reps':'8-12','descanso':'60-90s','cor':'#FF6B6B'},
        {'nome':'Força','series':'4-5','reps':'4-6','descanso':'120-180s','cor':'#4ECDC4'},
        {'nome':'Resistência','series':'2-3','reps':'15-20','descanso':'30-45s','cor':'#95E1D3'},
    ]
    return {'fase_atual': fases[fase_idx], 'treinos_restantes': TREINOS - treinos_no_ciclo, 'proxima_fase': fases[(fase_idx+1)%3], 'numero_ciclo': ciclo+1}

# ---------------------------
# Notifications on open
# ---------------------------
def check_notifications_on_open():
    notifs = []
    dados = st.session_state.get('dados_usuario') or {}
    dias_list = dados.get('dias_semana_list') or None
    if dias_list and st.session_state['settings'].get('notify_on_login', True):
        hoje = datetime.now().weekday()
        if hoje in dias_list:
            notifs.append({'tipo':'lembrete_treino','msg':'Hoje é dia de treino! Confira seu plano.'})
    for m in st.session_state.get('metas', []):
        prazo = m.get('prazo')
        try:
            prazo_dt = date.fromisoformat(prazo) if isinstance(prazo, str) else prazo
            dias = (prazo_dt - datetime.now().date()).days
            if 0 <= dias <= 3:
                notifs.append({'tipo':'meta','msg':f"Meta '{m.get('descricao')}' vence em {dias} dia(s)."})
        except:
            pass
    num_treinos = len(set(st.session_state.get('frequencia', [])))
    info = verificar_periodizacao(num_treinos)
    if info['treinos_restantes'] <= 0 and st.session_state.get('ciclo_atual') != info['numero_ciclo']:
        notifs.append({'tipo':'nova_fase','msg':f"👏 Novo ciclo iniciado: {info['fase_atual']['nome']} (Ciclo {info['numero_ciclo']})"})
        st.session_state['ciclo_atual'] = info['numero_ciclo']
    for t in (5,10,30,50,100):
        if num_treinos == t:
            notifs.append({'tipo':'conquista','msg':f"🎉 Você alcançou {t} treinos!"})
    st.session_state['notificacoes'] = notifs

# ---------------------------
# UI pieces
# ---------------------------
def show_logo_center():
    st.markdown("<div style='text-align:center;'><h1>🏋️ FitPro</h1><p>Seu Personal Trainer Digital</p></div>", unsafe_allow_html=True)

# ---------------------------
# Fallback for dialog-based confirm delete
# If st.dialog exists, we use it. Otherwise we'll show a modal form fallback.
# ---------------------------
def confirm_delete_photo_dialog(idx: int, uid: Optional[str]):
    if HAS_ST_DIALOG:
        # Using new st.dialog decorator
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
        # fallback modal-like form
        st.session_state['foto_a_excluir'] = idx
        st.session_state['confirm_excluir_foto'] = True

# ---------------------------
# Small plan generator
# ---------------------------
def gerar_treino_basico(nivel='Iniciante'):
    if nivel == 'Iniciante':
        return {
            'Treino A: Corpo Inteiro': pd.DataFrame({'Exercício':['Agachamento com Halteres','Supino Reto com Halteres','Remada Sentada (máquina)'],'Séries':['3'],'Repetições':['10-15'],'Descanso':['60s']}),
            'Treino B: Pernas/Core': pd.DataFrame({'Exercício':['Leg Press 45°','Rosca Direta com Halteres'],'Séries':['3'],'Repetições':['10-15'],'Descanso':['60s']})
        }
    else:
        return {
            'Treino A: Peito/Tríceps': pd.DataFrame({'Exercício':['Supino Reto com Halteres'],'Séries':['4'],'Repetições':['6-8'],'Descanso':['90s']})
        }

# ---------------------------
# Pages
# ---------------------------
def render_auth():
    show_logo_center()
    st.markdown("---")
    tab_login, tab_cad = st.tabs(["🔑 Login","📝 Cadastro"])
    with tab_login:
        with st.form("form_login"):
            username = st.text_input("E-mail ou 'demo'")
            senha = st.text_input("Senha", type='password')
            col1, col2 = st.columns([3,1])
            with col2:
                if st.form_submit_button("👁️ Modo Demo"):
                    ok, msg = verificar_credenciais_firebase('demo','demo123')
                    if ok:
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
                if not nome or len(nome.strip())<3:
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
                    ok,msg = criar_usuario_firebase(email.strip(), senha, nome.strip())
                    if ok:
                        st.success(msg)
                        st.info("Faça login agora.")
                    else:
                        st.error(msg)
    st.stop()

def render_main():
    # on open notifications
    check_notifications_on_open()
    # sidebar
    st.sidebar.title("🏋️ FitPro")
    st.sidebar.write(f"👤 {st.session_state.get('usuario_logado')}")
    if st.sidebar.button("🚪 Sair"):
        uid = st.session_state.get('user_uid')
        if uid:
            salvar_dados_usuario_firebase(uid)
        # clear session (keep db)
        keys = list(st.session_state.keys())
        for k in keys:
            if k != 'db':
                del st.session_state[k]
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.subheader("Configurações")
    theme = st.sidebar.selectbox("Tema", ["light","dark"], index=0 if st.session_state['settings'].get('theme','light')=='light' else 1)
    st.session_state['settings']['theme'] = theme
    notify_on_open = st.sidebar.checkbox("Notificações ao abrir", value=st.session_state['settings'].get('notify_on_login', True))
    st.session_state['settings']['notify_on_login'] = notify_on_open
    st.sidebar.checkbox("Modo offline (cache)", value=st.session_state.get('offline_mode', False), key='offline_mode')
    # admin quicklink
    if st.session_state.get('role') == 'admin':
        st.sidebar.success("👑 Admin")
        if st.sidebar.button("Painel Admin"):
            st.session_state['page'] = 'Admin'
    # show notifs
    if st.session_state.get('notificacoes'):
        for n in st.session_state['notificacoes']:
            if n['tipo']=='conquista':
                st.balloons()
                st.success(n['msg'])
            else:
                # show toast-like
                try:
                    st.toast(n['msg'])
                except Exception:
                    st.info(n['msg'])
    # navigation
    pages = ["Dashboard","Questionário","Meu Treino","Registrar Treino","Progresso","Fotos","Comparar Fotos","Medidas","Planejamento Semanal","Metas","Nutrição","Busca","Export/Backup"]
    if st.session_state.get('role') == 'admin':
        pages.append("Admin")
    page = st.selectbox("Navegação", pages)
    st.session_state['last_page'] = page
    # render page
    if page == "Dashboard":
        render_dashboard()
    elif page == "Questionário":
        render_questionario()
    elif page == "Meu Treino":
        render_meu_treino()
    elif page == "Registrar Treino":
        render_registrar_treino()
    elif page == "Progresso":
        render_progresso()
    elif page == "Fotos":
        render_fotos()
    elif page == "Comparar Fotos":
        render_comparar_fotos()
    elif page == "Medidas":
        render_medidas()
    elif page == "Planejamento Semanal":
        render_planner()
    elif page == "Metas":
        render_metas()
    elif page == "Nutrição":
        render_nutricao()
    elif page == "Busca":
        render_busca()
    elif page == "Export/Backup":
        render_export_backup()
    elif page == "Admin":
        render_admin_panel()
    else:
        st.write("Página em desenvolvimento.")

# ---------------------------
# Page implementations
# ---------------------------
def render_dashboard():
    st.title("📊 Dashboard")
    show_logo_center()
    dados = st.session_state.get('dados_usuario') or {}
    num_treinos = len(set(st.session_state.get('frequencia', [])))
    st.metric("Treinos Completos", num_treinos)
    # periodization banner
    if num_treinos > 0:
        info = verificar_periodizacao(num_treinos)
        fase = info['fase_atual']
        st.markdown(f"""
            <div style='padding:20px;border-radius:12px;background:linear-gradient(90deg,{fase['cor']},#ffffff);color:#111;'>
            <h3>🎯 Fase Atual: {fase['nome']} | Ciclo {info['numero_ciclo']}</h3>
            <p>{fase['reps']} reps · {fase['series']} séries · Descanso {fase['descanso']}</p>
            </div>
        """, unsafe_allow_html=True)
    # medidas graph
    if st.session_state.get('medidas'):
        dfm = pd.DataFrame(st.session_state['medidas'])
        dfm['data'] = pd.to_datetime(dfm['data'])
        fig = px.line(dfm, x='data', y='valor', color='tipo', markers=True, title='Evolução de Medidas')
        st.plotly_chart(fig, use_container_width=True)
    # calendar heatmap (next 30 days)
    st.subheader("📅 Calendário de Treinos (últimos 30 dias)")
    if st.session_state.get('frequencia'):
        hoje = datetime.now().date()
        ult30 = [hoje - timedelta(days=i) for i in range(30)]
        treinos_30 = set(st.session_state['frequencia'])
        df_cal = pd.DataFrame({'data': ult30})
        df_cal['treinou'] = df_cal['data'].isin(treinos_30).astype(int)
        df_cal['weekday'] = df_cal['data'].apply(lambda d: d.strftime('%a'))
        df_cal['week'] = df_cal['data'].apply(lambda d: d.isocalendar().week)
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
    show_logo_center()
    dados = st.session_state.get('dados_usuario') or {}
    with st.form("form_q"):
        col1, col2 = st.columns(2)
        with col1:
            nome = st.text_input("Nome completo", value=dados.get('nome',''))
            idade = st.number_input("Idade", 12, 100, value=dados.get('idade',25))
            peso = st.number_input("Peso (kg)", 30.0, 200.0, value=dados.get('peso',70.0), step=0.1)
            altura = st.number_input("Altura (cm)", 100.0, 250.0, value=dados.get('altura',170.0), step=0.1)
        with col2:
            nivel = st.selectbox("Nível", ["Iniciante","Intermediário/Avançado"], index=0 if not dados.get('nivel') else (0 if dados['nivel']=='Iniciante' else 1))
            objetivo = st.selectbox("Objetivo", ["Hipertrofia","Emagrecimento","Condicionamento"], index=0 if not dados.get('objetivo') else ["Hipertrofia","Emagrecimento","Condicionamento"].index(dados.get('objetivo','Hipertrofia')))
            dias = st.slider("Dias/semana", 2, 6, value=dados.get('dias_semana',3))
        restricoes = st.multiselect("Restrições", ["Lombar","Joelhos","Ombros","Cotovelos","Punhos"], default=dados.get('restricoes',[]))
        dias_list = st.multiselect("Dias da semana (opcional, 0=Seg)", list(range(7)), default=dados.get('dias_semana_list',[]))
        if st.form_submit_button("Salvar"):
            st.session_state['dados_usuario'] = {'nome':nome,'idade':idade,'peso':peso,'altura':altura,'nivel':nivel,'objetivo':objetivo,'dias_semana':dias,'restricoes':restricoes,'dias_semana_list':dias_list,'data_cadastro': iso_now()}
            hp = st.session_state.get('historico_peso', [])
            if not hp or hp[-1].get('peso') != peso:
                hp.append({'data': iso_now(), 'peso': peso})
                st.session_state['historico_peso'] = hp
            st.session_state['plano_treino'] = gerar_treino_basico(nivel)
            uid = st.session_state.get('user_uid')
            if uid:
                salvar_dados_usuario_firebase(uid)
            st.success("Perfil salvo e plano gerado.")
            st.rerun()

def render_meu_treino():
    st.title("💪 Meu Treino")
    plano = st.session_state.get('plano_treino')
    if not plano:
        st.info("Nenhum plano gerado ainda.")
        return
    # Present each treino as a card with icon and small chart if data exists
    for nome, df in plano.items():
        # card
        st.markdown(f"""
            <div style="
                background: linear-gradient(90deg,#fff,#f7fafc);
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 12px;
                box-shadow: 0 6px 18px rgba(0,0,0,0.06);
            ">
                <h3 style="margin:0;">🏷️ {nome}</h3>
                <p style="margin:0;color:#555;">{len(df)} exercício(s)</p>
            </div>
        """, unsafe_allow_html=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
        # show evolution chart for exercises in this treino
        hist = pd.DataFrame(st.session_state.get('historico_treinos', []))
        if not hist.empty:
            exs = df['Exercício'].tolist()
            df_plot = hist[hist['exercicio'].isin(exs)].copy()
            if not df_plot.empty:
                df_plot['data'] = pd.to_datetime(df_plot['data'])
                fig = px.line(df_plot, x='data', y='peso', color='exercicio', markers=True, title=f'Evolução de cargas - {nome}')
                st.plotly_chart(fig, use_container_width=True)

def render_registrar_treino():
    st.title("📝 Registrar Treino")
    with st.form("f_registrar"):
        data = st.date_input("Data", datetime.now().date())
        tipos = list(st.session_state.get('plano_treino', {}).keys()) + ["Cardio","Outro"] if st.session_state.get('plano_treino') else ["Cardio","Outro"]
        tipo = st.selectbox("Tipo", tipos)
        exercicio = st.selectbox("Exercício", [""] + sorted(list(EXERCICIOS_DB.keys())))
        c1,c2,c3 = st.columns(3)
        with c1:
            series = st.number_input("Séries",1,12,3)
        with c2:
            reps = st.number_input("Repetições",1,50,10)
        with c3:
            peso = st.number_input("Peso (kg)",0.0,500.0,0.0,0.5)
        obs = st.text_area("Observações")
        if st.form_submit_button("Registrar"):
            if not exercicio:
                st.error("Escolha um exercício.")
            else:
                novo = {'data': data.isoformat(), 'tipo': tipo, 'exercicio': exercicio, 'series': int(series), 'reps': int(reps), 'peso': float(peso), 'volume': int(series)*int(reps)*float(peso), 'observacoes': obs, 'timestamp': iso_now()}
                hist = st.session_state.get('historico_treinos', [])
                hist.append(novo)
                st.session_state['historico_treinos'] = hist
                freq = st.session_state.get('frequencia', [])
                if data not in freq:
                    freq.append(data)
                    st.session_state['frequencia'] = freq
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)
                st.success("✅ Treino registrado.")
                # quick feedback form
                with st.form("form_feedback_quick"):
                    st.subheader("Feedback rápido")
                    nota = st.slider("Dificuldade (1-5)", 1, 5, 3)
                    dor = st.checkbox("Teve dor/desconforto")
                    comentarios = st.text_area("Comentários (opcional)")
                    if st.form_submit_button("Enviar feedback"):
                        st.session_state['feedbacks'].append({'exercicio':exercicio,'nota':nota,'dor':dor,'comentarios':comentarios,'data':data.isoformat()})
                        if uid:
                            salvar_dados_usuario_firebase(uid)
                        st.success("Obrigado pelo feedback!")

def render_progresso():
    st.title("📈 Progresso")
    hist = st.session_state.get('historico_treinos', [])
    if not hist:
        st.info("Registre treinos para ver gráficos.")
        return
    df = pd.DataFrame(hist)
    df['data'] = pd.to_datetime(df['data'])
    vol = df.groupby(df['data'].dt.date)['volume'].sum().reset_index()
    fig = px.line(vol, x='data', y='volume', title='Volume por dia', markers=True)
    st.plotly_chart(fig, use_container_width=True)
    # detect simple plateau
    vol['rolling'] = vol['volume'].rolling(7, min_periods=1).mean()
    if len(vol['rolling']) >= 8:
        last = vol['rolling'].iloc[-1]
        prev = vol['rolling'].iloc[-8]
        if prev > 0 and abs(last - prev)/prev < 0.05:
            st.warning("Possível platô detectado (variação <5% nas últimas semanas).")

def render_fotos():
    st.title("📸 Fotos de Progresso")
    with st.expander("Adicionar nova foto"):
        uploaded = st.file_uploader("Selecione uma imagem (png/jpg)", type=['png','jpg','jpeg'])
        if uploaded:
            img = Image.open(uploaded).convert('RGB')
            st.image(img, caption='Preview', width=300)
            data_foto = st.date_input("Data da foto", datetime.now().date())
            peso_foto = st.number_input("Peso (kg)", min_value=20.0, value=st.session_state.get('dados_usuario',{}).get('peso',70.0), step=0.1)
            nota = st.text_area("Notas (opcional)")
            if st.button("💾 Salvar foto"):
                b64 = b64_from_pil(img)
                fotos = st.session_state.get('fotos_progresso', [])
                fotos.append({'data': data_foto.isoformat(), 'peso': float(peso_foto), 'imagem': b64, 'nota': nota, 'timestamp': iso_now()})
                st.session_state['fotos_progresso'] = fotos
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)
                st.success("Foto salva.")
                st.rerun()
    st.subheader("Galeria")
    fotos = sorted(st.session_state.get('fotos_progresso', []), key=lambda x: x.get('data',''), reverse=True)
    if not fotos:
        st.info("Nenhuma foto ainda.")
        return
    for i,f in enumerate(fotos):
        c1,c2,c3 = st.columns([1,3,1])
        with c1:
            try:
                st.image(base64.b64decode(f['imagem']), width=140)
            except Exception:
                st.write("Imagem inválida")
        with c2:
            st.write(f"📅 {f.get('data')}  ⚖️ {f.get('peso')}kg")
            if f.get('nota'):
                st.write(f"📝 {f.get('nota')}")
        with c3:
            if st.button("🗑️ Excluir", key=f"del_{i}", use_container_width=True):
                confirm_delete_photo_dialog(i, st.session_state.get('user_uid'))
    # fallback modal handling
    if st.session_state.get('confirm_excluir_foto'):
        st.warning("Deseja realmente excluir esta foto? (fallback modal)")
        ca, cb = st.columns(2)
        with ca:
            if st.button("❌ Cancelar"):
                st.session_state['confirm_excluir_foto'] = False
                st.session_state['foto_a_excluir'] = None
                st.rerun()
        with cb:
            if st.button("✅ Confirmar exclusão"):
                idx = st.session_state.get('foto_a_excluir')
                fotos = st.session_state.get('fotos_progresso', [])
                if idx is not None and idx < len(fotos):
                    del fotos[idx]
                    st.session_state['fotos_progresso'] = fotos
                    uid = st.session_state.get('user_uid')
                    if uid:
                        salvar_dados_usuario_firebase(uid)
                    st.success("Foto excluída.")
                st.session_state['confirm_excluir_foto'] = False
                st.session_state['foto_a_excluir'] = None
                st.rerun()

def render_comparar_fotos():
    st.title("🔍 Comparar Fotos")
    fotos = st.session_state.get('fotos_progresso', [])
    if len(fotos) < 2:
        st.info("Adicione pelo menos duas fotos para comparar.")
        return
    options = [f"{i} - {f['data']} - {f.get('peso')}kg" for i,f in enumerate(fotos)]
    sel = st.multiselect("Escolha duas fotos (antes, depois)", options, default=[options[-1], options[0]])
    if len(sel) != 2:
        st.info("Selecione exatamente duas fotos.")
        return
    idx1 = options.index(sel[0])
    idx2 = options.index(sel[1])
    img1 = pil_from_b64(fotos[idx1]['imagem'])
    img2 = pil_from_b64(fotos[idx2]['imagem'])
    col1,col2 = st.columns(2)
    with col1:
        st.image(img1, caption=f"Antes: {fotos[idx1]['data']}")
    with col2:
        st.image(img2, caption=f"Depois: {fotos[idx2]['data']}")
    alpha = st.slider("Alpha (0=antes,1=depois)", 0.0, 1.0, 0.5)
    blended = overlay_blend(img1, img2, alpha)
    st.image(blended, caption=f"Blend (alpha={alpha})", use_column_width=True)
    metrics = compare_images_metric(img1, img2)
    st.json(metrics)

def render_medidas():
    st.title("📏 Medidas Corporais")
    with st.form("form_med"):
        tipo = st.selectbox("Tipo", ['Cintura','Quadril','Braço','Coxa','Peito'])
        valor = st.number_input("Valor (cm)", min_value=10.0, max_value=300.0, value=80.0, step=0.1)
        data = st.date_input("Data", datetime.now().date())
        if st.form_submit_button("Salvar medida"):
            medidas = st.session_state.get('medidas', [])
            medidas.append({'tipo':tipo,'valor':float(valor),'data':data.isoformat()})
            st.session_state['medidas'] = medidas
            uid = st.session_state.get('user_uid')
            if uid:
                salvar_dados_usuario_firebase(uid)
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
    suggested = suggest_days(dias_sem)
    st.write("Sugestão de dias (0=Seg):", suggested)
    # render simple calendar for next 14 days
    hoje = datetime.now().date()
    dias = [hoje + timedelta(days=i) for i in range(14)]
    treinou = set(st.session_state.get('frequencia', []))
    df = pd.DataFrame({'data':dias, 'treinou':[1 if d in treinou else 0 for d in dias]})
    df['weekday'] = df['data'].dt.weekday
    df['week'] = df['data'].dt.isocalendar().week
    try:
        pivot = df.pivot(index='week', columns='weekday', values='treinou').fillna(0)
        fig = px.imshow(pivot, labels=dict(x='weekday', y='week', color='treinou'), text_auto=True)
        st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.table(df)

def suggest_days(dias_sem:int):
    if dias_sem <= 0:
        return []
    step = 7 / dias_sem
    days = [int(round(i*step))%7 for i in range(dias_sem)]
    return sorted(list(set(days)))

def render_metas():
    st.title("🎯 Metas")
    with st.form("form_meta"):
        descricao = st.text_input("Descrição")
        alvo = st.number_input("Valor Alvo", 0.0, format="%.1f")
        prazo = st.date_input("Prazo", min_value=datetime.now().date())
        if st.form_submit_button("Adicionar"):
            metas = st.session_state.get('metas', [])
            metas.append({'descricao':descricao,'valor_alvo':alvo,'prazo':prazo.isoformat(),'criada_em':iso_now(),'concluida':False})
            st.session_state['metas'] = metas
            uid = st.session_state.get('user_uid')
            if uid:
                salvar_dados_usuario_firebase(uid)
            st.success("Meta adicionada.")
    for i,m in enumerate(st.session_state.get('metas', [])):
        col1,col2 = st.columns([4,1])
        with col1:
            st.write(f"{m['descricao']} - Alvo: {m['valor_alvo']} - Prazo: {m['prazo']}")
        with col2:
            if st.button("✅ Concluir", key=f"conq_{i}"):
                st.session_state['metas'][i]['concluida'] = True
                uid = st.session_state.get('user_uid')
                if uid:
                    salvar_dados_usuario_firebase(uid)
                st.success("Meta concluída.")
                st.rerun()

def render_nutricao():
    st.title("🥗 Nutrição Básica")
    dados = st.session_state.get('dados_usuario') or {}
    sexo = st.selectbox("Sexo", ["Masculino","Feminino"])
    peso = st.number_input("Peso (kg)", value=dados.get('peso',70.0))
    altura = st.number_input("Altura (cm)", value=dados.get('altura',170.0))
    idade = st.number_input("Idade", value=dados.get('idade',25))
    objetivo = st.selectbox("Objetivo", ["Manutenção","Emagrecimento","Hipertrofia"])
    if st.button("Calcular TMB e macros"):
        tmb = calcular_tmb(sexo, peso, altura, idade)
        macros = sugerir_macros(tmb, objetivo)
        st.metric("TMB estimada", f"{int(tmb)} kcal/dia")
        st.write("Sugestão de macros:", macros)

def calcular_tmb(sexo: str, peso: float, altura_cm: float, idade: int) -> float:
    if sexo.lower().startswith('m'):
        return 10*peso + 6.25*altura_cm - 5*idade + 5
    else:
        return 10*peso + 6.25*altura_cm - 5*idade - 161

def sugerir_macros(tmb: float, objetivo: str):
    calorias = tmb * 1.55
    if objetivo == 'Emagrecimento':
        calorias *= 0.8
    elif objetivo == 'Hipertrofia':
        calorias *= 1.15
    peso = st.session_state.get('dados_usuario',{}).get('peso',70)
    prote = 1.8 * peso
    prote_kcal = prote * 4
    gord_kcal = calorias * 0.25
    gord = gord_kcal / 9
    carbs_kcal = calorias - (prote_kcal + gord_kcal)
    carbs = carbs_kcal / 4 if carbs_kcal>0 else 0
    return {'calorias': round(calorias), 'proteina_g': round(prote,1), 'gordura_g': round(gord,1), 'carbs_g': round(carbs,1)}

def render_busca():
    st.title("🔎 Busca")
    q = st.text_input("Pesquisar exercícios / histórico / treinos")
    if q:
        exs = [name for name in EXERCICIOS_DB.keys() if q.lower() in name.lower()]
        st.subheader("Exercícios encontrados")
        st.write(exs)
        hist = st.session_state.get('historico_treinos', [])
        matches = [h for h in hist if q.lower() in h.get('exercicio','').lower()]
        st.subheader("No histórico")
        st.dataframe(pd.DataFrame(matches))

def render_export_backup():
    st.title("📤 Export / Backup")
    payload = {
        'dados_usuario': st.session_state.get('dados_usuario'),
        'plano_treino': plan_to_serial(st.session_state.get('plano_treino')),
        'frequencia': st.session_state.get('frequencia'),
        'historico_treinos': st.session_state.get('historico_treinos'),
        'metas': st.session_state.get('metas'),
        'fotos_progresso': st.session_state.get('fotos_progresso'),
        'medidas': st.session_state.get('medidas', []),
    }
    js = json.dumps(payload, default=str, ensure_ascii=False)
    st.download_button("📥 Baixar backup JSON", data=js, file_name="fitpro_backup.json", mime="application/json")
    if st.session_state.get('historico_treinos'):
        df = pd.DataFrame(st.session_state['historico_treinos'])
        st.download_button("📥 Exportar histórico CSV", data=df.to_csv(index=False), file_name="historico_treinos.csv", mime="text/csv")
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
        st.error("Erro ao listar usuários (verifique conexão/firestore).")
        return
    st.write(f"Total usuários: {len(users)}")
    for u in users:
        d = u.to_dict()
        nome = d.get('username') or (d.get('dados_usuario') or {}).get('nome','-')
        st.write(f"- {nome} ({u.id}) - treinos: {len(d.get('frequencia', []))} - role: {d.get('role')}")
        c1,c2,c3 = st.columns([3,1,1])
        with c1:
            if st.button("Ver dados", key=f"ver_{u.id}"):
                st.json(d)
        with c2:
            if d.get('role') != 'admin' and st.button("Promover", key=f"prom_{u.id}"):
                db.collection('usuarios').document(u.id).update({'role':'admin'})
                st.success("Promovido a admin.")
                st.rerun()
        with c3:
            if st.button("Excluir", key=f"del_{u.id}"):
                st.session_state['user_to_delete'] = u.id
                st.session_state['confirm_delete_user'] = True
                st.rerun()
    if st.session_state.get('confirm_delete_user'):
        st.warning("Confirmar exclusão do usuário (irrevogável).")
        ca,cb = st.columns(2)
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
                st.session_state['confirm_delete_user'] = False
                st.session_state['user_to_delete'] = None
                st.rerun()
        with cb:
            if st.button("❌ Cancelar"):
                st.session_state['confirm_delete_user'] = False
                st.session_state['user_to_delete'] = None
                st.rerun()

# ---------------------------
# Utils used above
# ---------------------------
def plan_to_serial(plano):
    return plan_to_serial.__wrapped__(plano) if hasattr(plan_to_serial, "__wrapped__") else plan_to_serial(plano)

# (we already have plan_to_serial above; to avoid linter confusion, just rebind)
def plan_to_serial(plano):
    if not plano:
        return None
    out = {}
    for k,v in plano.items():
        if isinstance(v, pd.DataFrame):
            out[k] = v.to_dict(orient='records')
        else:
            out[k] = v
    return out

# ---------------------------
# Run app
# ---------------------------
def run():
    if not st.session_state.get('usuario_logado'):
        render_auth()
    else:
        render_main()

if __name__ == "__main__":
    run()
