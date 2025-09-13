import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import json
import requests
from io import StringIO

# Configuration de la page Streamlit
st.set_page_config(layout="wide")

st.title("Analyse de l'accessibilité des loyers à Paris")
st.markdown("""
Cette application vous permet de visualiser les quartiers de Paris accessibles en fonction de votre budget et de la surface souhaitée.
**Les données affichées correspondent aux derniers arrêtés en vigueur pour l'année 2025.**
""")

# URL vers le jeu de données open data
DATA_URL = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/logement-encadrement-des-loyers/exports/csv?lang=fr&timezone=Europe%2FParis&use_labels=true&delimiter=%3B"

@st.cache_data
def load_data():
    """
    Charge les données depuis l'API, les filtre pour l'année 2025, les nettoie et les met en cache.
    """
    try:
        response = requests.get(DATA_URL)
        response.raise_for_status()
        csv_data = StringIO(response.text)
        
        df = pd.read_csv(csv_data, sep=';')
        
        # Renommer les colonnes pour plus de simplicité
        df.rename(columns={
            "Année": "annee",
            "Secteurs géographiques": "secteur_geo",
            "Numéro du quartier": "num_quartier",
            "Nom du quartier": "nom_quartier",
            "Nombre de pièces principales": "nb_pieces",
            "Epoque de construction": "epoque_construction",
            "Type de location": "type_location",
            "Loyers de référence": "loyer_ref",
            "Loyers de référence majorés": "loyer_majore",
            "Loyers de référence minorés": "loyer_minore",
            "Numéro INSEE du quartier": "insee_quartier",
            "geo_shape": "geo_shape"
        }, inplace=True)

        # Filtre par année
        df = df[df['annee'] == 2025].copy()

        if df.empty:
            st.warning("Avertissement : Aucune donnée n'a été trouvée pour l'année 2025 dans la source. La carte sera vide.")
            return pd.DataFrame()

        # Nettoyage et conversion
        cols_loyer = ['loyer_ref', 'loyer_majore', 'loyer_minore']
        for col in cols_loyer:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '.'), errors='coerce')
        
        df.dropna(subset=cols_loyer + ['geo_shape', 'nom_quartier'], inplace=True)

        def safe_json_load(x):
            try:
                return json.loads(x)['coordinates']
            except (json.JSONDecodeError, TypeError, KeyError):
                return None
        
        df['geo_points'] = df['geo_shape'].apply(safe_json_load)
        df.dropna(subset=['geo_points'], inplace=True)
        
        return df
    except requests.exceptions.RequestException as e:
        st.error(f"Erreur lors du chargement des données : {e}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Une erreur inattendue est survenue : {e}")
        return pd.DataFrame()

# Chargement des données
data = load_data()

if not data.empty:
    # --- Barre latérale pour les inputs utilisateur ---
    st.sidebar.header("Vos critères de recherche")

    budget = st.sidebar.number_input(
        "Votre budget mensuel (€)", 
        min_value=300, 
        max_value=10000, 
        value=1500, 
        step=50
    )

    surface = st.sidebar.number_input(
        "Surface souhaitée (m²)",
        min_value=10,
        max_value=200,
        value=30,
        step=1
    )

    type_loc = st.sidebar.radio(
        "Type de location",
        options=sorted(data['type_location'].unique()),
        index=0
    )

    epoques_options = sorted(data['epoque_construction'].unique())
    epoque_selection = st.sidebar.multiselect(
        "Époque de construction",
        options=epoques_options,
        default=epoques_options
    )

    loyer_options = ['Loyers de référence majorés', 'Loyers de référence', 'Loyers de référence minorés']
    loyer_a_utiliser = st.sidebar.selectbox(
        "Type de loyer à considérer",
        options=loyer_options,
        index=0
    )

    col_loyer_map = {
        'Loyers de référence majorés': 'loyer_majore',
        'Loyers de référence': 'loyer_ref',
        'Loyers de référence minorés': 'loyer_minore'
    }
    col_loyer_selection = col_loyer_map[loyer_a_utiliser]

    # --- Filtrage et préparation des données pour la carte ---
    df_filtered = data[
        (data['type_location'] == type_loc) &
        (data['epoque_construction'].isin(epoque_selection))
    ].copy()
    
    if df_filtered.empty:
        st.warning("Aucun logement ne correspond à vos critères de sélection. Veuillez modifier vos filtres.")
    else:
        df_filtered['loyer_estime'] = df_filtered[col_loyer_selection] * surface
        df_filtered['dans_le_budget'] = df_filtered['loyer_estime'] <= budget

        # Agréger les informations par quartier
        quartiers_info = {}
        for name, group in df_filtered.groupby('nom_quartier'):
            is_accessible = group['dans_le_budget'].any()
            
            group_sorted = group.sort_values(['nb_pieces', 'loyer_estime'])
            
            # === MODIFICATION DE LA GÉNÉRATION DU HTML POUR LE POPUP ===
            tooltip_html = f"<b>{name}</b><hr>"
            for _, row in group_sorted.iterrows():
                check_icon = "✅" if row['dans_le_budget'] else "❌"
                
                line_content = (f"<b>{row['nb_pieces']} pièce</b> ({row['epoque_construction']}): "
                                f"{row[col_loyer_selection]:.2f} €/m² | "
                                f"Loyer: {row['loyer_estime']:.0f} € {check_icon}")
                
                # Ajout du style CSS pour forcer une seule ligne
                tooltip_html += f"<div style='white-space: nowrap;'>{line_content}</div>"
            # ==========================================================

            quartiers_info[name] = {
                'accessible': is_accessible,
                'tooltip': tooltip_html,
                'geo_points': group.iloc[0]['geo_points']
            }

        # --- Création de la carte Folium ---
        map_center = [48.8566, 2.3522]
        m = folium.Map(location=map_center, zoom_start=12, tiles="cartodbpositron")
        
        for quartier, info in quartiers_info.items():
            color = 'green' if info['accessible'] else 'red'
            
            try:
                points_inverted = [[point[1], point[0]] for point in info['geo_points'][0]]
                
                poly = folium.Polygon(
                    locations=points_inverted,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.4,
                    weight=2,
                    tooltip=f"<b>{quartier}</b>"
                )
                
                # === MODIFICATION DE LA LARGEUR DU POPUP ===
                popup = folium.Popup(
                    folium.Html(info['tooltip'], script=True), 
                    min_width=450, # Force une largeur minimale
                    max_width=600  # Permet une largeur maximale
                )
                # ============================================
                popup.add_to(poly)
                
                poly.add_to(m)
            except (TypeError, IndexError):
                pass

        # Affichage de la carte
        st_folium(m, use_container_width=True, height=600)
else:
    st.error("Les données n'ont pas pu être chargées ou aucune donnée n'est disponible pour 2025. Veuillez réessayer plus tard.")
