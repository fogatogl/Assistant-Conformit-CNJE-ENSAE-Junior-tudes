"""
app.py — Assistant Conformité CNJE — ENSAE Junior Études
=========================================================
Lancement : streamlit run app.py
"""

import streamlit as st
import yaml
from datetime import date, timedelta, datetime
from rules_engine import RulesEngine, MegaPromptGenerator
from firebase_bridge import FirebaseBridge, COLLECTIONS, HAS_FIREBASE

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PAGE
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Conformité CNJE — ENSAE JE",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# CSS CUSTOM (minimaliste, compatible dark mode Streamlit)
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Barre de progression des étapes */
.step-bar { display:flex; gap:8px; margin-bottom:1.5rem; }
.step-pill {
    padding:4px 14px; border-radius:20px; font-size:0.82rem;
    font-weight:500; border:1.5px solid transparent;
}
.step-pill.done    { background:#1d9e75; color:#fff; border-color:#1d9e75; }
.step-pill.active  { background:#185fa5; color:#fff; border-color:#185fa5; }
.step-pill.pending { background:transparent; color:#888; border-color:#888; }

/* Bloc erreur / avertissement */
.rule-error {
    background:#fef2f2; border-left:4px solid #dc2626;
    padding:10px 14px; border-radius:0 6px 6px 0;
    margin:6px 0; font-size:0.88rem;
}
.rule-warn {
    background:#fffbeb; border-left:4px solid #d97706;
    padding:10px 14px; border-radius:0 6px 6px 0;
    margin:6px 0; font-size:0.88rem;
}
.rule-ref { color:#6b7280; font-size:0.78rem; margin-top:3px; }

/* Récap financier */
.fin-table td { padding:4px 12px; }
.fin-table .label { color:#6b7280; font-size:0.88rem; }
.fin-table .value { font-weight:500; text-align:right; }
.fin-table .total { font-size:1.05rem; border-top:1px solid #e5e7eb; }

/* Mega-prompt textarea */
.prompt-box {
    font-family: monospace; font-size:0.82rem;
    background:#f8fafc; border:1px solid #e2e8f0;
    border-radius:8px; padding:14px; white-space:pre-wrap;
    max-height:420px; overflow-y:auto;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# CHARGEMENT RESSOURCES (cached)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def load_engine():
    return RulesEngine("rules_cnje.yaml")

@st.cache_resource
def load_generator():
    return MegaPromptGenerator("rules_cnje.yaml")

@st.cache_resource
def load_firebase_bridge():
    """Charge le bridge Firebase une seule fois (Service Account lourd à init)."""
    try:
        b = FirebaseBridge("serviceAccountKey.json")
        ok, msg = b.ping()
        if ok:
            return b, msg
        return None, msg
    except Exception as e:
        return None, str(e)

@st.cache_data(ttl=300)
def load_users_from_firebase():
    """Charge la liste des étudiants depuis Firestore (cache 5 min)."""
    bridge, _ = load_firebase_bridge()
    if bridge is None:
        return []
    try:
        return bridge.get_users()
    except Exception:
        return []

@st.cache_data
def load_yaml_meta():
    with open("rules_cnje.yaml", "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw.get("meta", {}), raw.get("mega_prompt_config", {})

engine    = load_engine()
generator = load_generator()
meta, _   = load_yaml_meta()

# ─────────────────────────────────────────────────────────────────────────────
# ÉTAT SESSION (navigation multi-étapes)
# ─────────────────────────────────────────────────────────────────────────────

STEPS = ["Étude", "Étapes & JEH", "Intervenants", "Financier", "Validation", "Export Tomate", "Prompt IA"]

def init_state():
    defaults = {
        "step":              0,
        "etude_nom":         "",
        "etude_but":         "",
        "etude_specs":       "",
        "etude_competences": "",
        "etude_domaines":    [],
        "etude_lieu":        1,
        "etude_statut_id":   0,
        "etude_locked":      False,
        "etude_has_child":   False,
        "p_jeh":             400,
        "per_rem":           65,
        "fee":               0.0,
        "break_jeh":         0,
        "break_fee":         0.0,
        "client":            None,
        "entreprise":        None,
        "signataire":        None,
        "etapes":            [],
        "docs":              [],
        "last_report":       None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — Référentiel & navigation
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://www.junior-entreprises.com/wp-content/uploads/2021/03/logo-cnje-1.png",
             width=160, use_container_width=False)
    st.markdown(f"**Assistant Conformité CNJE**  \nENSAE Junior Études")
    st.divider()

    st.markdown(f"**Référentiel chargé**  \nv{meta.get('version','?')} — {meta.get('date_derniere_maj','?')}")
    st.caption(f"Source : {meta.get('source_officielle','CNJE')}")

    st.divider()
    st.markdown("**Navigation**")
    for i, label in enumerate(STEPS):
        is_active = (i == st.session_state.step)
        icon = "▶" if is_active else ("✓" if i < st.session_state.step else "○")
        if st.button(f"{icon}  {label}", key=f"nav_{i}",
                     type="primary" if is_active else "secondary",
                     use_container_width=True):
            st.session_state.step = i
            st.rerun()

    st.divider()
    # Mini-récap financier en sidebar
    if st.session_state.etapes:
        total_jeh = sum(
            sum(se.get("jeh", 0) for se in e.get("sEtapes", []))
            for e in st.session_state.etapes
        )
        prix_ht  = total_jeh * st.session_state.p_jeh
        prix_eco = prix_ht * 0.01
        prix_tot = prix_ht + st.session_state.fee + prix_eco
        prix_ttc = prix_tot * 1.20

        st.markdown("**Récapitulatif**")
        st.metric("Total JEH", f"{total_jeh} JEH")
        st.metric("Montant HT", f"{prix_ht:,.0f} €")
        st.metric("Montant TTC", f"{prix_ttc:,.0f} €")


# ─────────────────────────────────────────────────────────────────────────────
# BARRE DE PROGRESSION
# ─────────────────────────────────────────────────────────────────────────────

def render_step_bar():
    pills = ""
    for i, label in enumerate(STEPS):
        if i < st.session_state.step:
            cls = "done"
        elif i == st.session_state.step:
            cls = "active"
        else:
            cls = "pending"
        pills += f'<span class="step-pill {cls}">{label}</span>'
    st.markdown(f'<div class="step-bar">{pills}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS COMMUNS
# ─────────────────────────────────────────────────────────────────────────────

DOMAINES = [
    "Statistique descriptive", "Analyse de données", "Enquête/Sondage",
    "Finance", "Économie", "Informatique", "Économétrie",
    "Séries temporelles", "Mathématiques", "Machine Learning",
]

LIEUX = {1: "À l'ENSAE", 2: "Chez le client"}
STATUTS = {0: "Brouillon", 1: "En attente", 2: "Recrutement",
           3: "Sélection", 4: "En cours", 5: "Clôturée"}


def build_etude_data() -> dict:
    """Assemble le dict etude_data depuis st.session_state."""
    return {
        "p_jeh":        st.session_state.p_jeh,
        "per_rem":      st.session_state.per_rem,
        "fee":          st.session_state.fee,
        "break_jeh":    st.session_state.break_jeh,
        "break_fee":    st.session_state.break_fee,
        "etude_statut_id": st.session_state.etude_statut_id,
        "locked":       st.session_state.etude_locked,
        "has_child":    st.session_state.etude_has_child,
        "date_created": date.today(),
        "client":       st.session_state.client,
        "entreprise":   st.session_state.entreprise,
        "signataire":   st.session_state.signataire,
        "etapes":       st.session_state.etapes,
        "docs":         st.session_state.docs,
        "admins":       [],
        # Champs textuels pour le prompt
        "but":          st.session_state.etude_but,
        "specifications": st.session_state.etude_specs,
        "domaines_labels": st.session_state.etude_domaines,
        "lieu_label":   LIEUX.get(st.session_state.etude_lieu, "?"),
    }


def render_validation_inline(report, show_ok: bool = True):
    """Affiche les erreurs/avertissements d'un rapport dans la page courante."""
    if report is None:
        return
    if report.is_valid and show_ok:
        st.success("Aucune erreur bloquante sur cette section.")
    for r in report.errors:
        ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
        st.markdown(
            f'<div class="rule-error">🔴 <strong>{r.categorie}</strong> — {r.message}{ref}</div>',
            unsafe_allow_html=True)
    for r in report.warnings:
        ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
        st.markdown(
            f'<div class="rule-warn">⚠️ <strong>{r.categorie}</strong> — {r.message}{ref}</div>',
            unsafe_allow_html=True)


def nav_buttons(prev_label="← Précédent", next_label="Suivant →",
                next_disabled=False):
    col1, _, col2 = st.columns([2, 5, 2])
    with col1:
        if st.session_state.step > 0:
            if st.button(prev_label, use_container_width=True):
                st.session_state.step -= 1
                st.rerun()
    with col2:
        if st.button(next_label, use_container_width=True,
                     type="primary", disabled=next_disabled):
            st.session_state.step += 1
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 0 — Informations générales de l'étude
# ─────────────────────────────────────────────────────────────────────────────

def step_etude():
    render_step_bar()
    st.subheader("Informations générales de l'étude")
    st.caption("Ces données alimenteront la convention et les documents officiels.")

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.etude_nom = st.text_input(
            "Nom de l'étude *",
            value=st.session_state.etude_nom,
            placeholder="Ex: Étude de marché secteur énergie",
        )
        st.session_state.etude_domaines = st.multiselect(
            "Domaine(s) d'intervention *",
            options=DOMAINES,
            default=st.session_state.etude_domaines,
        )
        st.session_state.etude_lieu = st.radio(
            "Lieu d'intervention",
            options=list(LIEUX.keys()),
            format_func=lambda x: LIEUX[x],
            horizontal=True,
            index=list(LIEUX.keys()).index(st.session_state.etude_lieu),
        )
        st.session_state.etude_statut_id = st.selectbox(
            "Statut actuel",
            options=list(STATUTS.keys()),
            format_func=lambda x: STATUTS[x],
            index=st.session_state.etude_statut_id,
        )
    with col2:
        st.session_state.etude_but = st.text_area(
            "Objectif / But de la mission",
            value=st.session_state.etude_but,
            height=120,
            placeholder="Décrire l'objectif principal de la prestation...",
            help="Ce champ sera utilisé pour générer le prompt IA à l'étape finale.",
        )
        st.session_state.etude_specs = st.text_area(
            "Spécifications détaillées",
            value=st.session_state.etude_specs,
            height=120,
            placeholder="Livrables, contraintes techniques, données fournies...",
        )
        st.session_state.etude_competences = st.text_area(
            "Compétences requises",
            value=st.session_state.etude_competences,
            height=80,
            placeholder="Python, R, statistiques, ...",
        )

    st.divider()
    st.subheader("Client et entreprise")
    col3, col4, col5 = st.columns(3)
    with col3:
        c_nom = st.text_input("Nom du client *",
                              value=(st.session_state.client or {}).get("nom",""))
        c_pre = st.text_input("Prénom du client",
                              value=(st.session_state.client or {}).get("prenom",""))
        if c_nom:
            st.session_state.client = {"id": 1, "nom": c_nom, "prenom": c_pre}
        else:
            st.session_state.client = None
    with col4:
        e_nom = st.text_input("Raison sociale *",
                              value=(st.session_state.entreprise or {}).get("nom",""))
        e_sir = st.text_input("SIRET",
                              value=(st.session_state.entreprise or {}).get("siret",""))
        if e_nom:
            st.session_state.entreprise = {"id": 1, "nom": e_nom, "siret": e_sir}
        else:
            st.session_state.entreprise = None
    with col5:
        s_nom = st.text_input("Signataire côté client *",
                              value=(st.session_state.signataire or {}).get("nom",""),
                              help="Personne qui signera la convention (peut être différente du client de référence)")
        if s_nom:
            st.session_state.signataire = {"id": 1, "nom": s_nom}
        else:
            st.session_state.signataire = None

    st.divider()
    nav_buttons(
        next_label="Étapes & JEH →",
        next_disabled=not st.session_state.etude_nom,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 — Étapes et répartition des JEH
# ─────────────────────────────────────────────────────────────────────────────

def step_etapes():
    render_step_bar()
    st.subheader("Étapes de la mission et répartition des JEH")
    st.caption("Chaque étape correspond à une phase de travail distincte.")

    # ---- Formulaire ajout d'étape ----
    with st.expander("➕ Ajouter une étape", expanded=not st.session_state.etapes):
        with st.form("form_add_etape", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                nom_e = st.text_input("Nom de l'étape *", placeholder="Ex: Collecte des données")
                det_e = st.text_area("Description", height=80, placeholder="Activités réalisées dans cette étape...")
            with c2:
                d_start = st.date_input("Date de début", value=date.today())
                d_end   = st.date_input("Date de fin",   value=date.today() + timedelta(weeks=4))
                n_etape = len(st.session_state.etapes) + 1

            submitted = st.form_submit_button("Ajouter l'étape", type="primary")
            if submitted and nom_e:
                st.session_state.etapes.append({
                    "nom":        nom_e,
                    "details":    det_e,
                    "date_start": d_start,
                    "date_end":   d_end,
                    "n":          n_etape,
                    "sEtapes":    [],
                })
                st.rerun()

    # ---- Affichage des étapes existantes ----
    if not st.session_state.etapes:
        st.info("Aucune étape ajoutée. Créez au moins une étape pour continuer.")
    else:
        to_delete = None
        for idx, etape in enumerate(st.session_state.etapes):
            n_jeh = sum(se.get("jeh", 0) for se in etape.get("sEtapes", []))
            with st.container(border=True):
                hcol1, hcol2, hcol3 = st.columns([5, 2, 1])
                with hcol1:
                    st.markdown(f"**Étape {idx+1} — {etape['nom']}**")
                    st.caption(f"{etape.get('date_start','?')} → {etape.get('date_end','?')}")
                with hcol2:
                    badge = "🟢" if n_jeh > 0 else "🔴"
                    st.metric("JEH assignés", f"{badge} {n_jeh} JEH")
                with hcol3:
                    if st.button("🗑", key=f"del_etape_{idx}", help="Supprimer"):
                        to_delete = idx

                if etape.get("details"):
                    st.caption(etape["details"])

                # Sous-formulaire assignation d'un étudiant à cette étape
                with st.expander(f"Assigner des JEH à l'étape {idx+1}"):
                    with st.form(f"form_jeh_{idx}", clear_on_submit=True):
                        fc1, fc2, fc3 = st.columns(3)
                        with fc1:
                            ed_nom = st.text_input("Nom de l'étudiant", key=f"ed_nom_{idx}")
                        with fc2:
                            ed_id = st.text_input("Firebase UID",
                                                  key=f"ed_id_{idx}",
                                                  placeholder="ex: 15A6lzEMS6c1ciBx0vArrroRpi03",
                                                  help="ID visible dans Firestore → collection users")
                        with fc3:
                            ed_jeh = st.number_input("Nombre de JEH", min_value=1,
                                                     max_value=50, step=1,
                                                     key=f"ed_jeh_{idx}")
                        if st.form_submit_button("Assigner", type="primary"):
                            st.session_state.etapes[idx]["sEtapes"].append({
                                "etudiant_id":  ed_id.strip(),
                                "etudiant_nom": ed_nom or f"Étudiant {ed_id[:8]}…" if ed_id else "Étudiant",
                                "jeh":          int(ed_jeh),
                                "level":        0,
                            })
                            st.rerun()

                # Tableau des JEH assignés
                if etape["sEtapes"]:
                    rows = []
                    for se in etape["sEtapes"]:
                        rows.append({
                            "Étudiant": se["etudiant_nom"],
                            "ID":       se["etudiant_id"],
                            "JEH":      se["jeh"],
                        })
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        if to_delete is not None:
            st.session_state.etapes.pop(to_delete)
            st.rerun()

    # Validation live sur cette section
    if st.session_state.etapes:
        report = engine.validate(build_etude_data())
        jeh_issues = [r for r in report.results
                      if "JEH" in r.rule_id or "date" in r.rule_id.lower()]
        if jeh_issues:
            st.divider()
            st.markdown("**Problèmes détectés sur cette section :**")
            for r in jeh_issues:
                sev = "rule-error" if r.is_error else "rule-warn"
                ico = "🔴" if r.is_error else "⚠️"
                ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
                st.markdown(
                    f'<div class="{sev}">{ico} <strong>{r.categorie}</strong> — {r.message}{ref}</div>',
                    unsafe_allow_html=True)

    st.divider()
    nav_buttons(next_disabled=not st.session_state.etapes)


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 2 — Récapitulatif intervenants
# ─────────────────────────────────────────────────────────────────────────────

def step_intervenants():
    render_step_bar()
    st.subheader("Récapitulatif des intervenants")
    st.caption("Synthèse automatique des JEH par étudiant, tous étapes confondus.")

    data = build_etude_data()
    from rules_engine import RulesEngine
    ctx = engine._build_context(data)
    jeh_par_ed = ctx.get("jeh_par_etudiant", {})

    if not jeh_par_ed:
        st.warning("Aucun étudiant n'a encore été assigné. Retournez à l'étape précédente.")
    else:
        p_jeh   = st.session_state.p_jeh
        per_rem = st.session_state.per_rem

        rows = []
        for eid, info in jeh_par_ed.items():
            rem = round((per_rem / 100.0) * p_jeh * info["total_jeh"], 2)
            rows.append({
                "Étudiant":   info["nom"],
                "Total JEH":  info["total_jeh"],
                "Rémunération brute (€)": rem,
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        total_jeh = sum(r["Total JEH"] for r in rows)
        total_rem = sum(r["Rémunération brute (€)"] for r in rows)
        st.metric("Total JEH équipe", f"{total_jeh} JEH")
        col1, col2 = st.columns(2)
        col1.metric("Rémunérations totales", f"{total_rem:,.2f} €")
        col2.metric("Prix HT total", f"{total_jeh * p_jeh:,.2f} €")

    # Validation live intervenants
    report = engine.validate(data)
    inter_issues = [r for r in report.results if "INTERV" in r.rule_id or "REM" in r.rule_id]
    if inter_issues:
        st.divider()
        st.markdown("**Problèmes détectés :**")
        for r in inter_issues:
            sev = "rule-error" if r.is_error else "rule-warn"
            ico = "🔴" if r.is_error else "⚠️"
            ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
            st.markdown(
                f'<div class="{sev}">{ico} <strong>{r.categorie}</strong> — {r.message}{ref}</div>',
                unsafe_allow_html=True)

    st.divider()
    nav_buttons()


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 3 — Paramètres financiers
# ─────────────────────────────────────────────────────────────────────────────

def step_financier():
    render_step_bar()
    st.subheader("Paramètres financiers")
    st.caption("Ces valeurs déterminent le montant facturé au client et la rémunération des intervenants.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Tarification**")
        st.session_state.p_jeh = st.number_input(
            "Prix par JEH (€ HT) *",
            min_value=0, max_value=5000, step=10,
            value=st.session_state.p_jeh,
            help="Plancher CNJE : 200 €/JEH minimum",
        )
        st.session_state.fee = st.number_input(
            "Frais de gestion (€ HT)",
            min_value=0.0, step=50.0,
            value=st.session_state.fee,
            help="Frais fixes ajoutés au prix HT",
        )

    with col2:
        st.markdown("**Rémunération étudiante**")
        st.session_state.per_rem = st.slider(
            "Taux de rémunération (%)",
            min_value=0, max_value=100, step=1,
            value=st.session_state.per_rem,
            help="Minimum CNJE : 35 % du prix HT/JEH. Défaut Tomate : 65 %.",
        )
        ctx_preview = engine._build_context(build_etude_data())
        rem_per_jeh = ctx_preview.get("rem_par_jeh", 0)
        st.info(f"Rémunération effective : **{rem_per_jeh:.2f} €/JEH**  \n"
                f"Soit {st.session_state.per_rem} % × {st.session_state.p_jeh} €")

    st.divider()
    st.subheader("Avenant (optionnel)")
    col3, col4 = st.columns(2)
    with col3:
        st.session_state.break_jeh = st.number_input(
            "JEH supplémentaires (avenant)", min_value=0, step=1,
            value=st.session_state.break_jeh,
        )
    with col4:
        st.session_state.break_fee = st.number_input(
            "Frais de gestion avenant (€)", min_value=0.0, step=50.0,
            value=st.session_state.break_fee,
        )

    st.divider()
    # Tableau récapitulatif financier
    ctx = engine._build_context(build_etude_data())
    st.subheader("Récapitulatif financier")

    data_fin = {
        "Prix HT":              ctx.get("prix_ht", 0),
        "Frais de gestion":     st.session_state.fee,
        "Cotisation CNJE (1%)": ctx.get("prix_eco", 0),
        "Total HT":             ctx.get("prix_tot", 0),
        "TVA (20%)":            ctx.get("prix_tva", 0),
        "Total TTC":            ctx.get("prix_ttc", 0),
    }

    rows_fin = []
    for label, val in data_fin.items():
        is_total = label in ("Total TTC",)
        rows_fin.append({"Ligne": label, "Montant (€)": f"{val:,.2f}"})
    st.dataframe(rows_fin, use_container_width=True, hide_index=True)

    # Validation live financière
    report = engine.validate(build_etude_data())
    fin_issues = [r for r in report.results
                  if any(k in r.rule_id for k in ["JEH_PRIX","REM","FEE","PRIX","REMUNERATION"])]
    if fin_issues:
        st.divider()
        st.markdown("**Problèmes détectés :**")
        for r in fin_issues:
            sev = "rule-error" if r.is_error else "rule-warn"
            ico = "🔴" if r.is_error else "⚠️"
            ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
            st.markdown(
                f'<div class="{sev}">{ico} <strong>{r.categorie}</strong> — {r.message}{ref}</div>',
                unsafe_allow_html=True)

    st.divider()
    nav_buttons()


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 4 — Validation complète CNJE
# ─────────────────────────────────────────────────────────────────────────────

def step_validation():
    render_step_bar()
    st.subheader("Validation CNJE — Rapport complet")

    data   = build_etude_data()
    report = engine.validate(data)
    st.session_state.last_report = report

    # Bandeau de statut global
    n_err  = len(report.errors)
    n_warn = len(report.warnings)
    if report.is_valid:
        if n_warn == 0:
            st.success("✅ **L'étude est conforme** — Aucune erreur bloquante, aucun avertissement.")
        else:
            st.success(f"✅ **L'étude est conforme** — Aucune erreur bloquante.")
            st.warning(f"⚠️ {n_warn} avertissement(s) à examiner avant signature.")
    else:
        st.error(f"🔴 **L'étude n'est PAS conforme** — {n_err} erreur(s) bloquante(s) à corriger.")
        st.caption("Retournez aux étapes précédentes pour corriger les erreurs avant de générer les documents.")

    # Barre de score
    total_checks = max(1, n_err + n_warn + 5)  # dénominateur approximatif
    score = max(0, 100 - n_err * 20 - n_warn * 5)
    st.progress(score / 100, text=f"Score de conformité estimé : {score}/100")

    st.divider()

    # Détail par catégorie
    by_cat = report.by_category
    if not by_cat:
        st.info("Toutes les règles CNJE sont satisfaites sur cet ensemble de données.")
    else:
        for cat, items in by_cat.items():
            with st.expander(f"**{cat}** — {len(items)} résultat(s)", expanded=True):
                for r in items:
                    sev = "rule-error" if r.is_error else "rule-warn"
                    ico = "🔴" if r.is_error else "⚠️"
                    ref = f'<div class="rule-ref">{r.ref_cnje}</div>' if r.ref_cnje else ""
                    st.markdown(
                        f'<div class="{sev}">{ico} [{r.rule_id}] {r.message}{ref}</div>',
                        unsafe_allow_html=True)

    st.divider()
    # Récapitulatif de l'étude
    with st.expander("Récapitulatif complet de l'étude"):
        ctx = engine._build_context(data)
        col1, col2, col3 = st.columns(3)
        col1.metric("Total JEH", f"{ctx['total_jeh']}")
        col2.metric("Prix HT", f"{ctx['prix_ht']:,.0f} €")
        col3.metric("Prix TTC", f"{ctx['prix_ttc']:,.0f} €")
        col1.metric("Intervenants", f"{len(ctx['jeh_par_etudiant'])}")
        col2.metric("Étapes", f"{len(data['etapes'])}")
        col3.metric("Taux rem.", f"{data['per_rem']} %")

    nav_buttons(
        next_label="Export vers Tomate →",
        next_disabled=not report.is_valid,
    )
    if not report.is_valid:
        st.caption("Le bouton 'Export' est disponible uniquement quand l'étude est valide (zéro erreur bloquante).")


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 5 — Générateur de Mega-Prompts
# ─────────────────────────────────────────────────────────────────────────────

def step_prompt():
    render_step_bar()
    st.subheader("Générateur de Prompt IA")
    st.markdown(
        "L'étude est valide. Génère un prompt pré-formaté, anonymisé et conforme CNJE "
        "à copier dans **Claude, Gemini ou ChatGPT** pour rédiger les textes de la mission."
    )

    data = build_etude_data()

    TEMPLATE_LABELS = {
        "PROMPT_DESCRIPTION_MISSION": "Description de la mission (but + spécifications)",
        "PROMPT_DESCRIPTION_ETAPE":   "Détail des étapes",
        "PROMPT_COMPETENCES":         "Compétences pour le recrutement",
    }

    selected = st.radio(
        "Quel texte souhaitez-vous rédiger ?",
        options=list(TEMPLATE_LABELS.keys()),
        format_func=lambda x: TEMPLATE_LABELS[x],
        horizontal=False,
    )

    st.divider()

    try:
        prompt = generator.generate(data, selected)
    except Exception as e:
        st.error(f"Erreur de génération : {e}")
        return

    st.markdown("**Prompt généré — Copiez ce texte dans votre IA**")
    st.code(prompt, language=None)

    # Bouton copie (Streamlit ne supporte pas clipboard natif — on affiche une textarea)
    st.caption("Sélectionnez tout le texte ci-dessus (Ctrl+A dans la zone) et copiez-le dans Claude, Gemini ou ChatGPT.")

    st.divider()
    with st.expander("Champs exclus du prompt (données confidentielles)"):
        st.markdown("""
Les données suivantes **n'apparaissent jamais** dans le prompt envoyé à l'IA externe :
- Nom et coordonnées du client
- Raison sociale et SIRET de l'entreprise
- Prix par JEH, frais de gestion, montants financiers
- Taux de rémunération

Ces données restent dans votre système et dans Tomate uniquement.
""")

    st.divider()
    st.markdown("**Prochaines étapes**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.info("1. Coller le prompt dans votre IA préférée.")
    with c2:
        st.info("2. Relire et valider le texte généré.")
    with c3:
        st.info("3. Copier le texte validé dans Tomate (champ 'But' ou 'Spécifications').")

    st.divider()
    nav_buttons(next_label="— Fin du parcours —", next_disabled=True)


def step_export():
    render_step_bar()
    st.subheader("Export vers Tomate")

    report = st.session_state.last_report
    if report is None or not report.is_valid:
        st.error("L'étude doit être validée (étape précédente) avant l'export.")
        nav_buttons(next_label="Prompt IA →", next_disabled=True)
        return

    bridge, bridge_msg = load_firebase_bridge()

    # ── Statut de la connexion Firebase ────────────────────────────
    if bridge is None:
        st.error(f"Connexion Firestore impossible : {bridge_msg}")
        with st.expander("Comment configurer le Service Account ?"):
            st.markdown("""
**Étapes dans Firebase Console :**

1. Ouvrir [console.firebase.google.com/project/tomate-eje](https://console.firebase.google.com/project/tomate-eje)
2. Roue dentée ⚙ → **Paramètres du projet** → onglet **Comptes de service**
3. Cliquer **Générer une nouvelle clé privée**
4. Sauvegarder le fichier sous le nom ****
5. Placer ce fichier dans le même dossier que 
6. Relancer l'application Streamlit
""")
        nav_buttons(next_label="Prompt IA →")
        return
    else:
        st.success(f"✅ {bridge_msg}")

    # ── Prévisualisation ────────────────────────────────────────────
    with st.expander("Données qui seront écrites dans Firestore"):
        etapes = st.session_state.get("etapes") or []
        preview = {
            "Collection etudes": {
                "nom":     st.session_state.get("etude_nom"),
                "p_jeh":   st.session_state.get("p_jeh"),
                "per_rem": st.session_state.get("per_rem"),
                "fee":     st.session_state.get("fee"),
                "domaines":st.session_state.get("etude_domaines"),
                "statut":  st.session_state.get("etude_statut_id"),
            },
            "Collection etapes": f"{len(etapes)} étape(s)",
            "Collection jeh_etape": f"{sum(len(e.get('sEtapes',[]))  for e in etapes)} ligne(s) JEH",
            "Collection clients": st.session_state.get("client"),
            "Collection entreprises": st.session_state.get("entreprise"),
        }
        st.json(preview)

    # ── Bouton export ───────────────────────────────────────────────
    st.divider()
    if st.button("Exporter vers Firestore (Tomate)", type="primary",
                 use_container_width=False):
        export_data = dict(st.session_state)

        with st.status("Export Firestore en cours…", expanded=True) as status:
            step_labels = {
                "entreprise": "Entreprise cliente",
                "client":     "Contact client",
                "signataire": "Signataire",
                "etude":      "Étude principale",
                "etapes":     "Étapes & JEH",
            }
            try:
                result = bridge.push_all(export_data)
            except Exception as e:
                st.error(f"Erreur inattendue : {e}")
                status.update(label="Erreur", state="error")
                nav_buttons(next_label="Prompt IA →")
                return

            for step in result.steps:
                label = step_labels.get(step.step, step.step)
                if step.ok:
                    eid = step.entity_id[:8] + "…" if step.entity_id else ""
                    id_str = f" (id: `{eid}`)" if eid else ""
                    st.write(f"✅ {label}{id_str}")
                else:
                    st.write(f"❌ {label} — {step.error_msg}")

            if result.success:
                status.update(label="Export réussi !", state="complete")
                st.success(f"Étude créée dans Firestore (id: `{result.etude_id}`)")
                if result.etude_url:
                    st.markdown(f"**[Ouvrir dans Tomate]({result.etude_url})**")
                st.info("Prochaine étape : générer le Prompt IA, copier les textes dans Tomate.")
                load_users_from_firebase.clear()
            else:
                status.update(label=f"Échec : {result.error_step}", state="error")
                st.error(result.last_error)
                if result.etude_id:
                    st.warning(f"Étude partiellement créée (id: `{result.etude_id}`). "
                               f"Compléter manuellement dans Tomate.")

    st.divider()
    nav_buttons(next_label="Prompt IA →")


# ─────────────────────────────────────────────────────────────────────────────
# ROUTEUR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

st.title("Assistant Conformité CNJE")
st.caption(f"ENSAE Junior Études — Référentiel v{meta.get('version','?')} ({meta.get('date_derniere_maj','?')})")

STEP_FUNCS = [
    step_etude,
    step_etapes,
    step_intervenants,
    step_financier,
    step_validation,
    step_export,
    step_prompt,
]

STEP_FUNCS[st.session_state.step]()