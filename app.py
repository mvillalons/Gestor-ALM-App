import streamlit as st
import pandas as pd
import plotly.express as px
import os
import io
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Usamos el permiso de solo lectura para buscar los archivos que subiste
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

@st.cache_resource
def authenticate_drive():
    creds = None
    # El archivo token.json guarda tu sesión para no pedirte clave cada vez
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

@st.cache_data
def load_data_from_drive(_service, file_name):
    # Buscar el archivo por nombre exacto en el Drive
    results = _service.files().list(q=f"name='{file_name}'", spaces='drive', fields='files(id, name)').execute()
    items = results.get('files', [])
    
    if not items:
        return None
    
    # Descargar el archivo directamente a la memoria (BytesIO)
    file_id = items[0]['id']
    request = _service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    
    fh.seek(0)
    return pd.read_csv(fh)

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor ALM Personal", layout="wide")
st.title("🏛️ Gestor ALM Personal (BYOS)")
st.markdown("Leyendo datos estructurados directamente desde tu bóveda en Google Drive...")

# --- MOTOR ALM ---
try:
    service = authenticate_drive()
    
    with st.spinner("Conectando con Google Drive y buscando archivos..."):
        df_pos = load_data_from_drive(service, 'ALM_Posiciones_Balance.csv')
        df_flujos = load_data_from_drive(service, 'ALM_Flujos_Settlement.csv')
    
    if df_pos is not None and df_flujos is not None:
        st.success("¡Datos sincronizados exitosamente desde Google Drive!")
        
        # --- PESTAÑA 1: BALANCE SHEET ---
        st.header("1. Balance Sheet (Exposición Actual)")
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Tu Exposición Efectiva (Notional)")
            st.dataframe(df_pos[['ID_Posicion', 'Descripcion', 'Clase_Activo_Pasivo', 'Exposicion_Total_Notional', 'Moneda_Indexacion']], use_container_width=True)
        
        with col2:
            fig_pie = px.pie(df_pos, values=df_pos['Exposicion_Total_Notional'].abs(), names='Clase_Activo_Pasivo', title="Distribución de Pasivos y Activos")
            st.plotly_chart(fig_pie, use_container_width=True)

        # --- PESTAÑA 2: SETTLEMENT SCHEDULE ---
        st.header("2. Proyección de Liquidez (Cash Flow Matching)")
        df_flujos_desc = df_flujos.merge(df_pos[['ID_Posicion', 'Descripcion']], on='ID_Posicion', how='left')
        flujo_mensual = df_flujos_desc.groupby('Fecha_Settlement')['Flujo_Caja_Total'].sum().reset_index()
        
        col3, col4 = st.columns([2, 1])
        
        with col3:
            fig_bar = px.bar(flujo_mensual, x='Fecha_Settlement', y='Flujo_Caja_Total', 
                             title="Flujo de Caja Neto Proyectado por Mes",
                             labels={'Flujo_Caja_Total': 'Liquidez Neta (CLP)'},
                             color='Flujo_Caja_Total', color_continuous_scale='RdYlGn')
            st.plotly_chart(fig_bar, use_container_width=True)
            
        with col4:
            st.subheader("Detalle de Desarrollo")
            mes_seleccionado = st.selectbox("Selecciona un mes para ver detalle:", df_flujos_desc['Fecha_Settlement'].unique())
            detalle_mes = df_flujos_desc[df_flujos_desc['Fecha_Settlement'] == mes_seleccionado]
            st.dataframe(detalle_mes[['Descripcion', 'Flujo_Caja_Total']], use_container_width=True)
            
    else:
        st.warning("No se encontraron los archivos en tu Google Drive. Revisa que los nombres sean exactos.")

except Exception as e:
    st.error(f"Error de conexión: {e}")