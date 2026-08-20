"""
Dashboard Alliance IT — Tickets + MDM (Streamlit)
===================================================
Requiere:
  pip install streamlit plotly pandas requests --break-system-packages

Correr con:
  python -m streamlit run dashboard.py
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------- CONFIG ----------------
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]
API_BASE = st.secrets["API_BASE"]
BOARD_ID = 2
DIAS_INACTIVO = 30  # umbral para considerar un dispositivo inactivo

# Paleta Alliance
AZUL = "#1B2A6B"
ROJO = "#A91E2C"
GRIS = "#4A4A4A"
PALETA = ["#4C80C7", "#A91E2C", "#2ECC71", "#F39C12", "#9B59B6", "#1ABC9C", "#E74C3C", "#34495E"]

COLORES_ESTADO = {
    "Resuelto": "#2ECC71", "Cerrado": "#95A5A6", "Nuevo": "#3498DB",
    "Abrir": "#E67E22", "Esperando": "#F1C40F", "Pausado": "#9B59B6",
}

# Tipos de dispositivo MDM
NODECLASS_LABELS = {
    "WINDOWS_WORKSTATION": "Windows PC",
    "WINDOWS_SERVER": "Windows Server",
    "MAC": "macOS",
    "LINUX_WORKSTATION": "Linux PC",
    "LINUX_SERVER": "Linux Server",
    "APPLE_IOS": "iPhone/iPad",
    "APPLE_IPADOS": "iPad",
    "ANDROID": "Android",
    "NMS_SWITCH": "Switch",
    "NMS_ROUTER": "Router",
    "NMS_FIREWALL": "Firewall",
    "NMS_AP": "Access Point",
    "NMS_PRINTER": "Impresora",
    "NMS_PHONE": "Teléfono IP",
    "VMWARE_VM_HOST": "VM Host",
    "VMWARE_VM_GUEST": "VM Guest",
    "CLOUD_MONITOR_TARGET": "Cloud Monitor",
}

MDM_MOBILE_CLASSES = {"APPLE_IOS", "APPLE_IPADOS", "ANDROID"}

# ── Categorización de tickets ──
MAPA_CATEGORIAS = {
    "Cableado": "Red/Conectividad", "Conectar nuevo enlace al Fortinet": "Red/Conectividad",
    "Configuracion de iphone": "Hardware", "Configurar iphone": "Hardware",
    "Enrolar iphone": "Hardware", "Excel desactivado": "Software/Apps",
    "Hacer requisicion de telcel": "Administración", "Requisicion telcel": "Administración",
    "Implementación de ClaraAI": "Software/Apps", "Implementación de KDSMovil": "Software/Apps",
}

PATRONES_AMPLIADOS = [
    (r"requisici[oó]n|telcel|totalplay|estafeta|firma (digital|electr[oó]nica)|gafete|nuevo ingreso", "Administración"),
    (r"project|autocad|adobe|hydra|revit|office|excel|quickbooks|qbooks|teams|pdf|ninjaone|"
     r"salesforce|sales force|software|programa|libre office|planner|vsc\b|granola|claude|"
     r"custom\b|books|idioma de (teclado|project)|antivirus|no imprime|portal nfpa|clara",
     "Software/Apps"),
    (r"plotter|monitor|iphone|enrolar|macbook|laptop|lap ?top|disco duro|\bssd\b|bateria|"
     r"reinicio de pc|tarjeta inalambrica|problemas? pc|no enciende|soporte tecnico|tv\b",
     "Hardware"),
    (r"doble factor|autenticador|autenticacion|iniciar sesion|cuenta (google|microsoft)|clave\b", "Accesos"),
    (r"wi-?fi|cableado|enlace al fortinet|coneccion remota|conexion remota|problema de conexion", "Red/Conectividad"),
]

TIPO_KEYWORDS = {
    "Administración": ["proveedor", "factura", "cotización", "contrato", "compra", "pago"],
    "Red/Conectividad": ["internet", "lento", "vpn", "wifi", "correo", "red"],
    "Software/Apps": ["no funciona", "error", "licencia", "instalar", "app"],
    "Hardware": ["equipo", "impresora", "pantalla", "computadora", "laptop"],
    "Accesos": ["contraseña", "password", "permisos", "habilitar", "acceso"],
}


def categorizar(asunto: str) -> str:
    a = (asunto or "").strip()
    if a in MAPA_CATEGORIAS:
        return MAPA_CATEGORIAS[a]
    al = a.lower()
    for patron, cat in PATRONES_AMPLIADOS:
        if re.search(patron, al):
            return cat
    for cat, kws in TIPO_KEYWORDS.items():
        if any(kw in al for kw in kws):
            return cat
    return "Otros/Solicitudes"


# ── API ──
def get_token() -> str:
    resp = requests.post(f"{API_BASE}/ws/oauth/token", data={
        "grant_type": "client_credentials", "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "scope": "monitoring",
    })
    if not resp.ok:
        st.error(f"Error de autenticación NinjaOne: {resp.status_code}")
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_tickets(token: str) -> list:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    tickets, seen = [], set()
    cursor = 0
    while True:
        resp = requests.post(f"{API_BASE}/v2/ticketing/trigger/board/{BOARD_ID}/run",
            headers=headers, json={"sortBy": [{"field": "createTime", "direction": "DESC"}],
            "filters": [], "pageSize": 200, "lastCursorId": cursor})
        resp.raise_for_status()
        data = resp.json()
        batch = data if isinstance(data, list) else data.get("data", data.get("tickets", []))
        meta = data.get("metadata", {}) if isinstance(data, dict) else {}
        nuevos = [t for t in batch if t.get("id") not in seen]
        if not batch or not nuevos:
            break
        for t in nuevos:
            seen.add(t.get("id"))
        tickets.extend(nuevos)
        if len(batch) < 200:
            break
        cursor = meta.get("lastCursorId") or batch[-1].get("id", cursor)
    return tickets


def get_devices(token: str) -> list:
    """Obtiene todos los dispositivos vía GET /v2/devices con paginación."""
    headers = {"Authorization": f"Bearer {token}"}
    devices = []
    after = 0
    while True:
        resp = requests.get(f"{API_BASE}/v2/devices",
            headers=headers, params={"pageSize": 200, "after": after})
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        devices.extend(batch)
        if len(batch) < 200:
            break
        after = batch[-1].get("id", after)
    return devices


@st.cache_data(ttl=900)
def cargar_datos():
    token = get_token()
    tickets = get_tickets(token)
    filas = []
    for t in tickets:
        asunto = t.get("summary", "") or t.get("subject", "")
        estado_obj = t.get("status", {})
        estado = (estado_obj.get("displayName", "") if isinstance(estado_obj, dict) else str(estado_obj)).strip()
        created = t.get("createTime") or t.get("createdAt")
        fecha = pd.to_datetime(created, unit="s", errors="coerce") if created else None
        filas.append({
            "ID": t.get("id"), "Asunto": asunto,
            "Departamento": t.get("organization", "") or "Sin depto",
            "Tipo": categorizar(asunto),
            "Solicitante": t.get("requester", "Desconocido"),
            "Técnico": t.get("assignedAppUser") or "Sin asignar",
            "Estado": estado, "Creado": fecha,
            "Fuente": t.get("source", ""),
        })
    return pd.DataFrame(filas)


@st.cache_data(ttl=900)
def cargar_dispositivos():
    token = get_token()
    devices = get_devices(token)
    ahora = datetime.now(timezone.utc).timestamp()
    filas = []
    for d in devices:
        nc = d.get("nodeClass", "UNKNOWN")
        last_contact = d.get("lastContact")
        created = d.get("created")
        offline = d.get("offline", True)

        # Calcular días sin contacto
        if last_contact:
            try:
                dias_sin = (ahora - float(last_contact)) / 86400
            except (ValueError, TypeError):
                dias_sin = None
        else:
            dias_sin = None

        # Fecha legible
        fecha_contact = None
        if last_contact:
            try:
                fecha_contact = datetime.fromtimestamp(float(last_contact), tz=timezone.utc)
            except (ValueError, TypeError):
                pass

        fecha_creado = None
        if created:
            try:
                fecha_creado = datetime.fromtimestamp(float(created), tz=timezone.utc)
            except (ValueError, TypeError):
                pass

        org_name = ""
        refs = d.get("references", {})
        if isinstance(refs, dict):
            org_ref = refs.get("organization", {})
            if isinstance(org_ref, dict):
                org_name = org_ref.get("name", "")

        filas.append({
            "ID": d.get("id"),
            "Nombre": d.get("displayName") or d.get("systemName") or "Sin nombre",
            "Sistema": d.get("systemName", ""),
            "DNS": d.get("dnsName", ""),
            "Clase": nc,
            "Tipo": NODECLASS_LABELS.get(nc, nc),
            "Es Móvil": nc in MDM_MOBILE_CLASSES,
            "Online": not offline,
            "Último Contacto": fecha_contact,
            "Días sin contacto": round(dias_sin, 1) if dias_sin is not None else None,
            "Registrado": fecha_creado,
            "Organización": org_name,
            "Estado Aprobación": d.get("approvalStatus", ""),
        })
    return pd.DataFrame(filas)


# ── CSS personalizado ──
def inyectar_css():
    st.markdown("""<style>
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1B2A6B 0%, #2d4a9e 100%);
        padding: 16px 20px; border-radius: 10px;
        color: white !important; box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }
    [data-testid="stMetric"] label { color: rgba(255,255,255,0.8) !important; font-size: 13px !important; }
    [data-testid="stMetric"] [data-testid="stMetricValue"] { color: white !important; font-size: 28px !important; }
    [data-testid="stMetric"] [data-testid="stMetricDelta"] { color: rgba(255,255,255,0.9) !important; }
    div[data-testid="stSidebar"] { background: #0e1a42; }
    div[data-testid="stSidebar"] * { color: white !important; }
    .block-container { padding-top: 1.5rem; }
    h1 { color: #1B2A6B !important; }
    button[data-baseweb="tab"] p { font-size: 18px !important; font-weight: 600 !important; }
    h3 { color: #333 !important; border-bottom: 2px solid #A91E2C; padding-bottom: 4px; }
    </style>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════
#                    LAYOUT
# ══════════════════════════════════════════════════
st.set_page_config(page_title="Alliance IT — Dashboard", page_icon="🔥", layout="wide")
inyectar_css()

# ── Sidebar ──
with st.sidebar:
    st.title("🔥 Alliance Fire")
    st.markdown("---")
    if st.button("🔄 Refrescar datos", use_container_width=True):
        cargar_datos.clear()
        cargar_dispositivos.clear()
        st.rerun()
    st.markdown("---")
    st.caption(f"Última carga: {time.strftime('%H:%M:%S')}")
    st.caption("Auto-refresco cada 15 min")

# ── Tabs principales ──
tab_tickets, tab_mdm = st.tabs(["🎫 Tickets", "📱 Dispositivos / MDM"])

# ══════════════════════════════════════════════════
#              TAB 1: TICKETS
# ══════════════════════════════════════════════════
with tab_tickets:
    df_raw = cargar_datos()

    # Filtros inline
    with st.expander("🔍 Filtros", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            if not df_raw["Creado"].dropna().empty:
                min_date = df_raw["Creado"].min().date()
                max_date = df_raw["Creado"].max().date()
            else:
                min_date = datetime.now().date() - timedelta(days=90)
                max_date = datetime.now().date()
            rango = st.date_input("Rango de fechas", value=(min_date, max_date),
                                  min_value=min_date, max_value=max_date, key="t_rango")
        with fc2:
            tecnicos = ["Todos"] + sorted(df_raw["Técnico"].unique().tolist())
            sel_tec = st.selectbox("Técnico", tecnicos, key="t_tec")
        with fc3:
            categorias_list = ["Todas"] + sorted(df_raw["Tipo"].unique().tolist())
            sel_cat = st.selectbox("Categoría", categorias_list, key="t_cat")
        with fc4:
            estados = ["Todos"] + sorted(df_raw["Estado"].unique().tolist())
            sel_est = st.selectbox("Estado", estados, key="t_est")

    # Aplicar filtros
    df = df_raw.copy()
    if len(rango) == 2:
        mask = df["Creado"].notna()
        df = df[mask & (df["Creado"].dt.date >= rango[0]) & (df["Creado"].dt.date <= rango[1])]
    if sel_tec != "Todos":
        df = df[df["Técnico"] == sel_tec]
    if sel_cat != "Todas":
        df = df[df["Tipo"] == sel_cat]
    if sel_est != "Todos":
        df = df[df["Estado"] == sel_est]

    st.title("📊 Dashboard SLA Tickets")
    st.caption(f"{len(df)} tickets mostrados de {len(df_raw)} totales")

    # KPIs
    total = len(df)
    cerrados = df["Estado"].str.lower().isin(["resuelto", "cerrado"]).sum()
    abiertos = total - cerrados
    tasa = round(cerrados / total * 100, 1) if total else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Tickets", f"{total:,}")
    k2.metric("Abiertos", f"{abiertos:,}")
    k3.metric("Cerrados", f"{cerrados:,}")
    k4.metric("Tasa de Cierre", f"{tasa}%")
    st.markdown("---")

    # Fila 1
    col1, col2 = st.columns([3, 2])
    with col1:
        st.subheader("Carga por Técnico")
        df_tec = df.copy()
        df_tec["Grupo"] = df_tec["Estado"].str.lower().isin(["resuelto", "cerrado"]).map({True: "Cerrados", False: "Abiertos"})
        conteo = df_tec.groupby(["Técnico", "Grupo"]).size().reset_index(name="Tickets")
        fig = px.bar(conteo, x="Técnico", y="Tickets", color="Grupo", barmode="stack",
                     color_discrete_map={"Abiertos": "#4C80C7", "Cerrados": "#2ECC71"}, text="Tickets")
        fig.update_traces(textposition="inside", textfont_size=11)
        fig.update_layout(legend=dict(orientation="h", y=1.12), margin=dict(t=40, b=0), height=380,
                          xaxis_tickangle=-30, plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Distribución por Estado")
        estado_cnt = df["Estado"].value_counts().reset_index()
        estado_cnt.columns = ["Estado", "Tickets"]
        colores = [COLORES_ESTADO.get(e, "#BDC3C7") for e in estado_cnt["Estado"]]
        fig = go.Figure(go.Pie(labels=estado_cnt["Estado"], values=estado_cnt["Tickets"],
                               hole=0.5, marker_colors=colores, textinfo="label+percent", textposition="outside"))
        fig.update_layout(showlegend=False, margin=dict(t=30, b=0, l=0, r=0), height=380)
        st.plotly_chart(fig, use_container_width=True)

    # Fila 2
    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Top Categorías")
        cat_cnt = df["Tipo"].value_counts().reset_index()
        cat_cnt.columns = ["Categoría", "Tickets"]
        fig = px.bar(cat_cnt, y="Categoría", x="Tickets", orientation="h",
                     color="Tickets", color_continuous_scale=["#d6e4f0", "#4C80C7"], text="Tickets")
        fig.update_traces(textposition="outside")
        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(t=10, b=0),
                          height=350, plot_bgcolor="rgba(0,0,0,0)", yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
    with col4:
        st.subheader("Tickets por Departamento")
        dept_cnt = df["Departamento"].value_counts().reset_index()
        dept_cnt.columns = ["Departamento", "Tickets"]
        fig = px.treemap(dept_cnt, path=["Departamento"], values="Tickets",
                         color="Tickets", color_continuous_scale=["#f0e6e8", "#A91E2C"])
        fig.update_layout(margin=dict(t=10, b=0, l=0, r=0), height=350, coloraxis_showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Fila 3
    col5, col6 = st.columns([3, 2])
    with col5:
        st.subheader("Tendencia de Volumen")
        df_vol = df.dropna(subset=["Creado"]).copy()
        if not df_vol.empty:
            vol = df_vol.groupby(df_vol["Creado"].dt.date).size().reset_index(name="Tickets")
            vol.columns = ["Fecha", "Tickets"]
            vol["Fecha"] = pd.to_datetime(vol["Fecha"])
            vol = vol.sort_values("Fecha")
            vol["Media Móvil 7d"] = vol["Tickets"].rolling(7, min_periods=1).mean().round(1)
            fig = go.Figure()
            fig.add_trace(go.Bar(x=vol["Fecha"], y=vol["Tickets"], name="Diario", marker_color="rgba(76,128,199,0.4)"))
            fig.add_trace(go.Scatter(x=vol["Fecha"], y=vol["Media Móvil 7d"], name="Media 7d",
                                     line=dict(color="#A91E2C", width=2.5)))
            fig.update_layout(legend=dict(orientation="h", y=1.1), margin=dict(t=30, b=0), height=320,
                              plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False),
                              yaxis=dict(showgrid=True, gridcolor="#eee"))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin datos para el rango seleccionado")
    with col6:
        st.subheader("Mapa de Calor Semanal")
        df_heat = df.dropna(subset=["Creado"]).copy()
        if not df_heat.empty:
            dias_es = {0: "Lun", 1: "Mar", 2: "Mié", 3: "Jue", 4: "Vie", 5: "Sáb", 6: "Dom"}
            df_heat["Día"] = df_heat["Creado"].dt.dayofweek.map(dias_es)
            df_heat["Semana"] = df_heat["Creado"].dt.isocalendar().week.astype(int)
            pivot = df_heat.groupby(["Día", "Semana"]).size().reset_index(name="Tickets")
            pivot_table = pivot.pivot_table(index="Día", columns="Semana", values="Tickets", fill_value=0)
            orden = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
            pivot_table = pivot_table.reindex([d for d in orden if d in pivot_table.index])
            fig = px.imshow(pivot_table, color_continuous_scale=["#f7f7f7", "#1B2A6B"],
                            aspect="auto", labels=dict(x="Semana", y="Día", color="Tickets"))
            fig.update_layout(margin=dict(t=10, b=0, l=0, r=0), height=320, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin datos")

    # Fila 4
    col7, col8 = st.columns(2)
    with col7:
        st.subheader("Top 10 Solicitantes")
        sol_cnt = df["Solicitante"].value_counts().head(10).reset_index()
        sol_cnt.columns = ["Solicitante", "Tickets"]
        fig = px.bar(sol_cnt, y="Solicitante", x="Tickets", orientation="h",
                     color_discrete_sequence=[AZUL], text="Tickets")
        fig.update_traces(textposition="outside")
        fig.update_layout(margin=dict(t=10, b=0), height=320, plot_bgcolor="rgba(0,0,0,0)",
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
    with col8:
        st.subheader("Canal de Origen")
        fuente_map = {"HELP_REQUEST": "Solicitud de ayuda", "TECHNICIAN": "Técnico", "END_USER": "Usuario final"}
        df_f = df.copy()
        df_f["Canal"] = df_f["Fuente"].map(fuente_map).fillna(df_f["Fuente"])
        f_cnt = df_f["Canal"].value_counts().reset_index()
        f_cnt.columns = ["Canal", "Tickets"]
        fig = go.Figure(go.Pie(labels=f_cnt["Canal"], values=f_cnt["Tickets"],
                               hole=0.55, marker_colors=PALETA, textinfo="label+percent"))
        fig.update_layout(showlegend=False, margin=dict(t=10, b=0, l=0, r=0), height=320)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    with st.expander("📋 Ver detalle completo de tickets", expanded=False):
        st.dataframe(df.sort_values("Creado", ascending=False), use_container_width=True, height=400)


# ══════════════════════════════════════════════════
#              TAB 2: DISPOSITIVOS / MDM
# ══════════════════════════════════════════════════
with tab_mdm:
    dv = cargar_dispositivos()

    st.title("📱 Inventario de Dispositivos & MDM")

    if dv.empty:
        st.warning("No se pudieron obtener dispositivos de la API. Verifica que tu app OAuth tenga scope 'monitoring'.")
        st.stop()

    # Filtros MDM
    with st.expander("🔍 Filtros", expanded=False):
        mf1, mf2, mf3 = st.columns(3)
        with mf1:
            tipos_disp = ["Todos"] + sorted(dv["Tipo"].unique().tolist())
            sel_tipo_d = st.selectbox("Tipo de dispositivo", tipos_disp, key="m_tipo")
        with mf2:
            orgs = ["Todas"] + sorted([o for o in dv["Organización"].unique() if o])
            sel_org = st.selectbox("Organización", orgs, key="m_org")
        with mf3:
            sel_status = st.selectbox("Estado", ["Todos", "Online", "Offline"], key="m_status")

    # Aplicar filtros
    dv_f = dv.copy()
    if sel_tipo_d != "Todos":
        dv_f = dv_f[dv_f["Tipo"] == sel_tipo_d]
    if sel_org != "Todas":
        dv_f = dv_f[dv_f["Organización"] == sel_org]
    if sel_status == "Online":
        dv_f = dv_f[dv_f["Online"]]
    elif sel_status == "Offline":
        dv_f = dv_f[~dv_f["Online"]]

    st.caption(f"{len(dv_f)} dispositivos mostrados de {len(dv)} totales")

    # ── KPIs generales ──
    total_d = len(dv_f)
    online_d = dv_f["Online"].sum()
    offline_d = total_d - online_d
    moviles = dv_f["Es Móvil"].sum()
    inactivos = dv_f[dv_f["Días sin contacto"].notna() & (dv_f["Días sin contacto"] > DIAS_INACTIVO)]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Dispositivos", f"{total_d:,}")
    m2.metric("Online", f"{int(online_d):,}", delta=f"{round(online_d/total_d*100)}%" if total_d else "0%")
    m3.metric("Offline", f"{int(offline_d):,}")
    m4.metric("Móviles MDM", f"{int(moviles):,}")
    m5.metric(f"Inactivos (+{DIAS_INACTIVO}d)", f"{len(inactivos):,}", delta="⚠️" if len(inactivos) > 0 else "✅",
              delta_color="inverse")

    st.markdown("---")

    # ── Fila 1: Tipo de dispositivo + Online/Offline donut ──
    mc1, mc2 = st.columns([3, 2])

    with mc1:
        st.subheader("Dispositivos por Tipo")
        tipo_cnt = dv_f["Tipo"].value_counts().reset_index()
        tipo_cnt.columns = ["Tipo", "Cantidad"]
        fig = px.bar(tipo_cnt, y="Tipo", x="Cantidad", orientation="h",
                     color="Cantidad", color_continuous_scale=["#d6e4f0", "#4C80C7"], text="Cantidad")
        fig.update_traces(textposition="outside")
        fig.update_layout(showlegend=False, coloraxis_showscale=False, margin=dict(t=10, b=0),
                          height=400, plot_bgcolor="rgba(0,0,0,0)", yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)

    with mc2:
        st.subheader("Estado de Conexión")
        estado_d = pd.DataFrame({"Estado": ["Online", "Offline"], "Cantidad": [int(online_d), int(offline_d)]})
        fig = go.Figure(go.Pie(labels=estado_d["Estado"], values=estado_d["Cantidad"],
                               hole=0.55, marker_colors=["#2ECC71", "#E74C3C"],
                               textinfo="label+value+percent"))
        fig.update_layout(showlegend=False, margin=dict(t=30, b=0, l=0, r=0), height=400)
        st.plotly_chart(fig, use_container_width=True)

    # ── Fila 2: Por organización + Dispositivos móviles MDM ──
    mc3, mc4 = st.columns(2)

    with mc3:
        st.subheader("Dispositivos por Organización")
        org_cnt = dv_f[dv_f["Organización"] != ""]["Organización"].value_counts().reset_index()
        org_cnt.columns = ["Organización", "Dispositivos"]
        if not org_cnt.empty:
            fig = px.treemap(org_cnt, path=["Organización"], values="Dispositivos",
                             color="Dispositivos", color_continuous_scale=["#f0e6e8", "#A91E2C"])
            fig.update_layout(margin=dict(t=10, b=0, l=0, r=0), height=350, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Sin datos de organización")

    with mc4:
        st.subheader("Flota Móvil (MDM)")
        moviles_df = dv_f[dv_f["Es Móvil"]]
        if not moviles_df.empty:
            mob_cnt = moviles_df["Tipo"].value_counts().reset_index()
            mob_cnt.columns = ["Plataforma", "Cantidad"]
            fig = go.Figure(go.Pie(labels=mob_cnt["Plataforma"], values=mob_cnt["Cantidad"],
                                   hole=0.5, marker_colors=["#4C80C7", "#2ECC71", "#F39C12"],
                                   textinfo="label+value+percent"))
            fig.update_layout(showlegend=False, margin=dict(t=30, b=0, l=0, r=0), height=350)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No se encontraron dispositivos móviles enrolados en MDM")

    # ── Fila 3: Dispositivos duplicados + Inactivos ──
    mc5, mc6 = st.columns(2)

    with mc5:
        st.subheader("⚠️ Nombres Duplicados")
        nombres_dup = dv_f["Nombre"].value_counts()
        nombres_dup = nombres_dup[nombres_dup > 1].reset_index()
        nombres_dup.columns = ["Nombre", "Veces"]
        if not nombres_dup.empty:
            st.dataframe(nombres_dup.sort_values("Veces", ascending=False), use_container_width=True, height=300)
            st.caption(f"{len(nombres_dup)} nombres que aparecen más de una vez")
        else:
            st.success("No se encontraron dispositivos con nombre duplicado")

    with mc6:
        st.subheader(f"🔴 Inactivos (+{DIAS_INACTIVO} días)")
        if not inactivos.empty:
            cols_show = ["Nombre", "Tipo", "Organización", "Días sin contacto", "Último Contacto"]
            st.dataframe(inactivos[cols_show].sort_values("Días sin contacto", ascending=False),
                         use_container_width=True, height=300)
            st.caption(f"{len(inactivos)} dispositivos sin contacto en más de {DIAS_INACTIVO} días")
        else:
            st.success(f"Todos los dispositivos se han conectado en los últimos {DIAS_INACTIVO} días")

    # ── Timeline de registros ──
    st.markdown("---")
    st.subheader("Dispositivos Registrados por Mes")
    dv_reg = dv_f.dropna(subset=["Registrado"]).copy()
    if not dv_reg.empty:
        dv_reg["Mes"] = dv_reg["Registrado"].dt.to_period("M").astype(str)
        reg_cnt = dv_reg.groupby("Mes").size().reset_index(name="Nuevos")
        reg_cnt["Acumulado"] = reg_cnt["Nuevos"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=reg_cnt["Mes"], y=reg_cnt["Nuevos"], name="Nuevos/mes",
                             marker_color="rgba(76,128,199,0.5)"))
        fig.add_trace(go.Scatter(x=reg_cnt["Mes"], y=reg_cnt["Acumulado"], name="Acumulado",
                                 line=dict(color=ROJO, width=2.5), yaxis="y2"))
        fig.update_layout(
            yaxis=dict(title="Nuevos", showgrid=True, gridcolor="#eee"),
            yaxis2=dict(title="Acumulado", overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.1), margin=dict(t=30, b=0), height=300,
            plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False))
        st.plotly_chart(fig, use_container_width=True)

    # ── Tabla detalle ──
    with st.expander("📋 Ver inventario completo", expanded=False):
        cols_all = ["ID", "Nombre", "Tipo", "Clase", "Online", "Organización",
                    "Último Contacto", "Días sin contacto", "Registrado", "Estado Aprobación"]
        st.dataframe(dv_f[cols_all].sort_values("Último Contacto", ascending=False),
                     use_container_width=True, height=400)
