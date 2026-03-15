"""
tomate_bridge.py — Pont HTTP entre l'Assistant CNJE et Tomate
=============================================================

Envoie les données d'une étude validée directement dans Tomate
via ses endpoints AJAX existants. Aucune modification de Tomate requise.

FLUX D'APPEL ORDONNÉ :
  bridge = TomateBridge(base_url, email, password)
  result = bridge.push_all(streamlit_session_data)
  # → TomatePushResult avec l'URL de l'étude créée

ENDPOINTS TOMATE UTILISÉS (lus depuis routes.config) :
  POST  /Auth/AJAX/SignIn/          → authentification, cookie de session
  POST  /Ajax/SaveEntreprise/       → crée/met à jour l'entreprise cliente
  POST  /Ajax/SaveClient/           → crée/met à jour le contact client
  POST  /Ajax/SaveEtude/            → crée/met à jour l'étude principale
  POST  /Ajax/SaveEtapes/           → crée/met à jour les étapes + JEH

DÉPENDANCE : requests (pip install requests)
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

try:
    import requests
    from requests import Session, Response
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping Streamlit → Tomate
# (IDs extraits de Etude.php, Entreprise.php — ne pas modifier sauf refacto)
# ---------------------------------------------------------------------------

# modules/admin/entity/Etude.py::$domaineArray
DOMAINES_LABEL_TO_ID: dict[str, int] = {
    "Statistique descriptive":  1,
    "Analyse de données":       2,
    "Enquête/Sondage":          3,
    "Finance":                  4,
    "Économie":                 5,
    "Informatique":             6,
    "Économétrie":              7,
    "Séries temporelles":       8,
    "Mathématiques":            9,
    "Machine Learning":         10,
}

# modules/admin/entity/Entreprise.py::$secteursArray
SECTEURS_LABEL_TO_ID: dict[str, int] = {
    "Banque, finance, Assurance":        0,
    "Audit / Conseil":                   1,
    "Industrie":                         2,
    "Énergie":                           3,
    "Construction":                      4,
    "Transport & logistique":            5,
    "Services":                          6,
    "Informatique & Télécommunication":  7,
}

# modules/admin/entity/Entreprise.py::$typesArray
TYPES_ENT_TO_ID: dict[str, int] = {
    "Grand groupe":       0,
    "Institution publique": 1,
    "Association":        2,
    "PME":                3,
}


# ---------------------------------------------------------------------------
# Types de retour
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Résultat d'une étape du push (entreprise, client, étude, étapes)."""
    ok: bool
    step: str
    entity_id: int | None = None
    entity_data: dict = field(default_factory=dict)
    error_msg: str = ""


@dataclass
class TomatePushResult:
    """Résultat complet du push vers Tomate."""
    success: bool
    etude_id: int | None = None
    etude_numero: int | None = None
    etude_url: str | None = None
    steps: list[StepResult] = field(default_factory=list)
    error_step: str | None = None
    error_msg: str = ""

    @property
    def last_error(self) -> str:
        if self.error_msg:
            return self.error_msg
        failed = [s for s in self.steps if not s.ok]
        return failed[-1].error_msg if failed else ""


# ---------------------------------------------------------------------------
# Classe principale
# ---------------------------------------------------------------------------

class TomateBridge:
    """
    Client HTTP pour l'API interne de Tomate.

    Usage :
        bridge = TomateBridge("https://tomate-dev.ensaejunioretudes.fr", "admin@eje.fr", "mdp")
        result = bridge.push_all(session_data)
    """

    # Route de login (modules/auth/config/routes.config)
    LOGIN_PATH = "/Auth/AJAX/SignIn/"

    def __init__(self, base_url: str, email: str, password: str):
        if not HAS_REQUESTS:
            raise ImportError(
                "Le module 'requests' est requis : pip install requests"
            )
        self.base_url  = base_url.rstrip("/")
        self.email     = email
        self.password  = password
        self._session: Session | None = None
        self._logged_in = False

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------

    def _get_session(self) -> Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Content-Type": "application/json",
                "Accept":       "application/json",
                "X-Requested-With": "XMLHttpRequest",
            })
        return self._session

    def login(self) -> StepResult:
        """
        POST /Auth/AJAX/SignIn/   (route définie dans modules/auth/config/routes.config)
        Tomate retourne {"res": true, "url": "/..."} et pose PHPSESSID.
        allow_redirects=True suit le redirect HTTP→HTTPS si base_url est en HTTP.
        """
        session = self._get_session()
        url = f"{self.base_url}{self.LOGIN_PATH}"
        try:
            resp = session.post(
                url,
                json={"mail": self.email, "password": self.password},
                timeout=10,
                allow_redirects=True,
            )
            data = self._parse_response(resp, "login")
        except Exception as e:
            return StepResult(ok=False, step="login", error_msg=f"Connexion impossible : {e}")

        if not data.get("res"):
            return StepResult(
                ok=False, step="login",
                error_msg=data.get("msg", "Identifiants invalides")
            )
        self._logged_in = True
        return StepResult(ok=True, step="login")

    def _ensure_logged_in(self) -> StepResult | None:
        """Login automatique si pas encore authentifié. Retourne un StepResult d'erreur ou None."""
        if not self._logged_in:
            r = self.login()
            if not r.ok:
                return r
        return None

    # ------------------------------------------------------------------
    # Helpers HTTP
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        session = self._get_session()
        url = f"{self.base_url}/{path.lstrip('/')}"
        logger.debug("POST %s — payload keys: %s", url, list(payload.keys()))
        resp = session.post(url, json=payload, timeout=15)
        return self._parse_response(resp, path)

    @staticmethod
    def _parse_response(resp: Response, context: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise ValueError(
                f"[{context}] Réponse non-JSON (HTTP {resp.status_code}) : "
                f"{resp.text[:200]}"
            )
        if resp.status_code == 403:
            raise PermissionError(
                f"[{context}] Accès refusé (403). "
                "Vérifier que l'utilisateur a le niveau admin (level ≥ 2) dans Tomate."
            )
        if resp.status_code >= 500:
            raise RuntimeError(
                f"[{context}] Erreur serveur Tomate (HTTP {resp.status_code})"
            )
        return data

    @staticmethod
    def _fmt_date(d) -> str | None:
        """Convertit une date Python en string ISO attendu par Tomate."""
        if d is None:
            return None
        if isinstance(d, datetime):
            return d.strftime("%Y-%m-%d")
        if isinstance(d, date):
            return d.strftime("%Y-%m-%d")
        return str(d)

    # ------------------------------------------------------------------
    # Push Entreprise
    # ------------------------------------------------------------------

    def push_entreprise(self, entreprise: dict) -> StepResult:
        """
        POST /Ajax/SaveEntreprise/
        Champs acceptés : id, nom, type, secteur, presentation
        """
        auth_err = self._ensure_logged_in()
        if auth_err: return auth_err

        payload: dict[str, Any] = {
            "id":           entreprise.get("id") or None,
            "nom":          entreprise.get("nom", ""),
            "type":         entreprise.get("type_id", 3),       # défaut : PME
            "secteur":      entreprise.get("secteur_id", 6),    # défaut : Services
            "presentation": entreprise.get("presentation", ""),
        }

        try:
            data = self._post("/Ajax/SaveEntreprise/", payload)
        except Exception as e:
            return StepResult(ok=False, step="entreprise", error_msg=str(e))

        if not data.get("res"):
            return StepResult(
                ok=False, step="entreprise",
                error_msg=data.get("msg", "Erreur lors de la sauvegarde de l'entreprise")
            )

        ent_data = data.get("entreprise", {})
        ent_id   = ent_data.get("id") or (ent_data.get("id") if isinstance(ent_data, dict) else None)
        return StepResult(ok=True, step="entreprise",
                          entity_id=ent_id, entity_data=ent_data)

    # ------------------------------------------------------------------
    # Push Client
    # ------------------------------------------------------------------

    def push_client(self, client: dict, entreprise_id: int | None = None) -> StepResult:
        """
        POST /Ajax/SaveClient/
        Champs : id, nom, prenom, mail, titre, adresse, code_postal,
                 fixe, mobile, ville, last_contact, entreprise
        """
        auth_err = self._ensure_logged_in()
        if auth_err: return auth_err

        payload: dict[str, Any] = {
            "id":           client.get("id") or None,
            "nom":          client.get("nom", ""),
            "prenom":       client.get("prenom", ""),
            "mail":         client.get("mail", ""),
            "titre":        client.get("titre", 1),
            "adresse":      client.get("adresse", ""),
            "code_postal":  client.get("code_postal", ""),
            "ville":        client.get("ville", ""),
            "fixe":         client.get("fixe", ""),
            "mobile":       client.get("mobile", ""),
            "last_contact": client.get("last_contact", 1),
            "entreprise":   entreprise_id or client.get("entreprise_id"),
        }

        try:
            data = self._post("/Ajax/SaveClient/", payload)
        except Exception as e:
            return StepResult(ok=False, step="client", error_msg=str(e))

        if not data.get("res"):
            return StepResult(
                ok=False, step="client",
                error_msg=data.get("msg", "Erreur lors de la sauvegarde du client")
            )

        cl_data = data.get("client", {})
        cl_id   = cl_data.get("id") if isinstance(cl_data, dict) else None
        return StepResult(ok=True, step="client",
                          entity_id=cl_id, entity_data=cl_data)

    # ------------------------------------------------------------------
    # Push Étude (sans les étapes)
    # ------------------------------------------------------------------

    def push_etude(
        self,
        session_data: dict,
        client_id:      int | None,
        entreprise_id:  int | None,
        signataire_id:  int | None = None,
    ) -> StepResult:
        """
        POST /Ajax/SaveEtude/
        Construit le payload complet de l'étude depuis session_data.
        """
        auth_err = self._ensure_logged_in()
        if auth_err: return auth_err

        # Conversion domaines labels → IDs Tomate
        domaines_labels = session_data.get("etude_domaines", []) or []
        domaines_ids = [
            DOMAINES_LABEL_TO_ID[label]
            for label in domaines_labels
            if label in DOMAINES_LABEL_TO_ID
        ]

        payload: dict[str, Any] = {
            # Identité
            "id":          None,           # toujours null = création
            "nom":         session_data.get("etude_nom", ""),
            "pseudo":      None,
            "numero":      0,              # auto-généré par Tomate (generateNum())

            # Textes
            "but":         session_data.get("etude_but", ""),
            "but_short":   None,
            "specifications": session_data.get("etude_specs", ""),
            "competences": session_data.get("etude_competences", ""),
            "bdd":         None,
            "context":     None,
            "pub":         None,
            "pub_titre":   None,
            "notes":       None,
            "avn_motif":   None,

            # Financier
            "p_jeh":       int(session_data.get("p_jeh", 400)),
            "per_rem":     int(session_data.get("per_rem", 65)),
            "fee":         float(session_data.get("fee", 0)),
            "break_jeh":   int(session_data.get("break_jeh", 0)),
            "break_fee":   float(session_data.get("break_fee", 0)),

            # Statuts (IDs entiers)
            "statut":      int(session_data.get("etude_statut_id", 0)),
            "compt_statut": 0,
            "lieu":        int(session_data.get("etude_lieu", 1)),
            "provenance":  None,
            "locked":      bool(session_data.get("etude_locked", False)),

            # Domaines (tableau d'IDs)
            "domaines":    domaines_ids,

            # Relations (IDs)
            "client":      client_id,
            "facturation": client_id,      # par défaut = même que client
            "signataire":  signataire_id or client_id,
            "entreprise":  entreprise_id,
            "admins":      [],             # à renseigner manuellement dans Tomate si besoin
        }

        try:
            data = self._post("/Ajax/SaveEtude/", payload)
        except Exception as e:
            return StepResult(ok=False, step="etude", error_msg=str(e))

        if not data.get("res"):
            return StepResult(
                ok=False, step="etude",
                error_msg=data.get("msg", "Erreur lors de la sauvegarde de l'étude")
            )

        etude_data = data.get("etude", {})
        if isinstance(etude_data, dict):
            etude_id = etude_data.get("id")
        else:
            etude_id = None

        return StepResult(ok=True, step="etude",
                          entity_id=etude_id, entity_data=etude_data)

    # ------------------------------------------------------------------
    # Push Étapes
    # ------------------------------------------------------------------

    def push_etapes(self, etude_id: int, etapes: list[dict]) -> StepResult:
        """
        POST /Ajax/SaveEtapes/
        Payload : {etude_id: int, etapes: [...]}
        Format étape : {nom, details, date_start, date_end, n, sEtapes: [{etudiant, jeh}]}
        """
        auth_err = self._ensure_logged_in()
        if auth_err: return auth_err

        etapes_payload = []
        for i, e in enumerate(etapes):
            s_etapes = []
            for se in e.get("sEtapes", []):
                s_etapes.append({
                    "id":       None,
                    "etudiant": se.get("etudiant_id"),
                    "jeh":      int(se.get("jeh", 1)),
                    "etape":    None,   # sera rempli par Tomate après save
                })

            etapes_payload.append({
                "id":         None,
                "nom":        e.get("nom", f"Étape {i+1}"),
                "details":    e.get("details", ""),
                "date_start": self._fmt_date(e.get("date_start")),
                "date_end":   self._fmt_date(e.get("date_end")),
                "n":          i + 1,
                "etude":      etude_id,
                "sEtapes":    s_etapes,
            })

        payload = {
            "etude_id": etude_id,
            "etapes":   etapes_payload,
        }

        try:
            data = self._post("/Ajax/SaveEtapes/", payload)
        except Exception as e:
            return StepResult(ok=False, step="etapes", error_msg=str(e))

        if not data.get("res"):
            return StepResult(
                ok=False, step="etapes",
                error_msg=data.get("msg", "Erreur lors de la sauvegarde des étapes")
            )

        return StepResult(ok=True, step="etapes",
                          entity_data=data.get("etapes", {}))

    # ------------------------------------------------------------------
    # Orchestration complète
    # ------------------------------------------------------------------

    def push_all(self, session_data: dict) -> TomatePushResult:
        """
        Pipeline complet : entreprise → client → étude → étapes.

        session_data est le dict construit par app.py::build_export_data(),
        qui reprend st.session_state.
        """
        result = TomatePushResult(success=False)

        # ── 1. Login ──────────────────────────────────────────────────
        step = self.login()
        result.steps.append(step)
        if not step.ok:
            result.error_step = "login"
            result.error_msg  = step.error_msg
            return result

        # ── 2. Entreprise ─────────────────────────────────────────────
        entreprise_raw = session_data.get("entreprise") or {}
        if entreprise_raw.get("nom"):
            step = self.push_entreprise(entreprise_raw)
            result.steps.append(step)
            if not step.ok:
                result.error_step = "entreprise"
                result.error_msg  = step.error_msg
                return result
            entreprise_id = step.entity_id
        else:
            entreprise_id = None

        # ── 3. Client ─────────────────────────────────────────────────
        client_raw = session_data.get("client") or {}
        if client_raw.get("nom"):
            step = self.push_client(client_raw, entreprise_id=entreprise_id)
            result.steps.append(step)
            if not step.ok:
                result.error_step = "client"
                result.error_msg  = step.error_msg
                return result
            client_id = step.entity_id
        else:
            client_id = None

        # Signataire : même personne que le client par défaut
        # Si différent, créer un second client Tomate
        signataire_raw = session_data.get("signataire") or {}
        if (signataire_raw.get("nom") and
                signataire_raw.get("nom") != client_raw.get("nom")):
            step = self.push_client(signataire_raw, entreprise_id=entreprise_id)
            result.steps.append(step)
            if not step.ok:
                result.error_step = "signataire"
                result.error_msg  = step.error_msg
                return result
            signataire_id = step.entity_id
        else:
            signataire_id = client_id

        # ── 4. Étude ──────────────────────────────────────────────────
        step = self.push_etude(
            session_data,
            client_id=client_id,
            entreprise_id=entreprise_id,
            signataire_id=signataire_id,
        )
        result.steps.append(step)
        if not step.ok:
            result.error_step = "etude"
            result.error_msg  = step.error_msg
            return result

        etude_id     = step.entity_id
        etude_numero = step.entity_data.get("numero")

        # ── 5. Étapes ─────────────────────────────────────────────────
        etapes = session_data.get("etapes", []) or []
        if etapes and etude_id:
            step = self.push_etapes(etude_id, etapes)
            result.steps.append(step)
            if not step.ok:
                result.error_step = "etapes"
                result.error_msg  = step.error_msg
                # L'étude a été créée — on retourne quand même l'URL pour correction manuelle
                result.etude_id     = etude_id
                result.etude_numero = etude_numero
                result.etude_url    = self._build_edit_url(etude_id)
                return result

        # ── Succès ────────────────────────────────────────────────────
        result.success      = True
        result.etude_id     = etude_id
        result.etude_numero = etude_numero
        result.etude_url    = self._build_edit_url(etude_id)
        return result

    def _build_edit_url(self, etude_id: int | None) -> str | None:
        if not etude_id:
            return None
        return f"{self.base_url}/Admin/Suivi/{etude_id}/"

    # ------------------------------------------------------------------
    # Test de connectivité
    # ------------------------------------------------------------------

    def ping(self) -> tuple[bool, str]:
        """
        Vérifie que Tomate est accessible et que les credentials fonctionnent.
        Teste d'abord la page de login (GET /SignIn/) puis POST /Auth/AJAX/SignIn/.
        """
        if not HAS_REQUESTS:
            return False, "Module 'requests' non installé."
        try:
            session = self._get_session()
            # On teste la page de login publique (level 0 dans routes.config)
            resp = session.get(
                f"{self.base_url}/SignIn/",
                timeout=8,
                allow_redirects=True,
            )
            # 200 = page de login affichée, 302 = redirect (ex. déjà connecté)
            # Tomate peut aussi retourner du HTML avec un 200 si la route n'existe pas
            # → on vérifie que ce n'est pas une erreur serveur
            if resp.status_code >= 500:
                return False, f"Tomate inaccessible (HTTP {resp.status_code})"
            if resp.status_code == 404:
                return False, (
                    f"Route /SignIn/ introuvable (404). "
                    f"Vérifier que l'URL de base est correcte : {self.base_url}"
                )
        except Exception as e:
            return False, f"Hôte inaccessible : {e}"

        login_result = self.login()
        if not login_result.ok:
            return False, f"Authentification échouée : {login_result.error_msg}"

        return True, "Connexion à Tomate réussie."


# ---------------------------------------------------------------------------
# Intégration Streamlit (widget autonome)
# ---------------------------------------------------------------------------

def render_export_widget(session_state) -> TomatePushResult | None:
    """
    Affiche le formulaire de connexion Tomate et le bouton d'export.
    À appeler depuis app.py à l'étape "Export".

    Retourne le TomatePushResult si l'export a été lancé, None sinon.
    """
    try:
        import streamlit as st
    except ImportError:
        raise ImportError("streamlit requis pour render_export_widget()")

    if not HAS_REQUESTS:
        st.error("Le module Python `requests` n'est pas installé. "
                 "Lancer : `pip install requests`")
        return None

    st.markdown("### Connexion à Tomate")
    st.caption("Renseigner les informations de votre instance Tomate. Ces données ne sont pas sauvegardées.")

    col1, col2 = st.columns(2)
    with col1:
        tomate_url = st.text_input(
            "URL de Tomate",
            value=st.session_state.get("_tomate_url", "http://localhost/tomate"),
            placeholder="https://tomate.ensaeje.fr",
            key="_tomate_url_input",
        )
        st.session_state["_tomate_url"] = tomate_url

    with col2:
        tomate_email = st.text_input(
            "Email admin Tomate",
            value=st.session_state.get("_tomate_email", ""),
            key="_tomate_email_input",
        )
        tomate_pwd = st.text_input(
            "Mot de passe",
            type="password",
            key="_tomate_pwd_input",
        )

    # Bouton test de connectivité
    col_ping, col_export, _ = st.columns([2, 3, 2])
    with col_ping:
        if st.button("Tester la connexion", use_container_width=True):
            bridge = TomateBridge(tomate_url, tomate_email, tomate_pwd)
            ok, msg = bridge.ping()
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    st.divider()

    # Prévisualisation du payload
    with st.expander("Prévisualisation des données qui seront envoyées à Tomate"):
        preview = {
            "nom":       session_state.get("etude_nom"),
            "p_jeh":     session_state.get("p_jeh"),
            "per_rem":   session_state.get("per_rem"),
            "fee":       session_state.get("fee"),
            "domaines":  session_state.get("etude_domaines"),
            "lieu":      session_state.get("etude_lieu"),
            "client":    session_state.get("client"),
            "entreprise":session_state.get("entreprise"),
            "n_etapes":  len(session_state.get("etapes", [])),
            "total_jeh": sum(
                sum(se.get("jeh", 0) for se in e.get("sEtapes", []))
                for e in (session_state.get("etapes") or [])
            ),
        }
        st.json(preview)

    # Export principal
    with col_export:
        export_btn = st.button(
            "Exporter vers Tomate",
            type="primary",
            use_container_width=True,
            disabled=not (tomate_url and tomate_email and tomate_pwd),
        )

    if export_btn:
        # Construction du dict d'export depuis session_state
        export_data = {k: v for k, v in vars(session_state).items()
                       if not k.startswith("_")}

        bridge  = TomateBridge(tomate_url, tomate_email, tomate_pwd)
        results = TomatePushResult(success=False)

        with st.status("Export en cours…", expanded=True) as status:
            step_labels = {
                "login":        "Authentification",
                "entreprise":   "Création de l'entreprise",
                "client":       "Création du client",
                "signataire":   "Création du signataire",
                "etude":        "Création de l'étude",
                "etapes":       "Envoi des étapes & JEH",
            }

            try:
                results = bridge.push_all(export_data)
            except Exception as e:
                st.error(f"Erreur inattendue : {e}")
                return None

            # Affichage des étapes
            for step in results.steps:
                label = step_labels.get(step.step, step.step)
                if step.ok:
                    st.write(f"✅ {label}")
                else:
                    st.write(f"❌ {label} — {step.error_msg}")

            if results.success:
                status.update(label="Export réussi !", state="complete")
            else:
                status.update(label=f"Échec à l'étape : {results.error_step}", state="error")

        return results

    return None


# ---------------------------------------------------------------------------
# CLI de test (python tomate_bridge.py --help)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Test du pont Tomate")
    parser.add_argument("--url",      required=True, help="URL base de Tomate (ex: http://localhost/tomate)")
    parser.add_argument("--email",    required=True, help="Email admin")
    parser.add_argument("--password", required=True, help="Mot de passe admin")
    parser.add_argument("--dry-run",  action="store_true", help="Tester la connexion sans pousser de données")
    args = parser.parse_args()

    bridge = TomateBridge(args.url, args.email, args.password)

    print(f"\nTest de connexion à {args.url}...")
    ok, msg = bridge.ping()
    print(f"{'[OK]' if ok else '[ÉCHEC]'} {msg}")

    if not ok or args.dry_run:
        sys.exit(0 if ok else 1)

    # Données de test minimales
    from datetime import date as dt
    test_data = {
        "etude_nom":    "TEST — Étude de démo (à supprimer)",
        "etude_but":    "Étude créée automatiquement par tomate_bridge.py pour test.",
        "etude_specs":  "Supprimer cette étude après vérification.",
        "etude_competences": "Python",
        "etude_domaines": ["Machine Learning"],
        "etude_lieu":   1,
        "etude_statut_id": 0,
        "etude_locked": False,
        "p_jeh": 400, "per_rem": 65, "fee": 0.0,
        "break_jeh": 0, "break_fee": 0.0,
        "client":     {"nom": "TEST_Client",    "prenom": "Test"},
        "entreprise": {"nom": "TEST_Entreprise"},
        "signataire": {"nom": "TEST_Client",    "prenom": "Test"},
        "etapes": [{
            "nom": "Phase test", "details": "Étape de test",
            "date_start": dt(2025, 3, 1), "date_end": dt(2025, 4, 1),
            "sEtapes": [],
        }],
    }

    print("\nPush de données de test vers Tomate...")
    result = bridge.push_all(test_data)

    print(f"\n{'=== SUCCÈS ===' if result.success else '=== ÉCHEC ==='}")
    for step in result.steps:
        icon = "✓" if step.ok else "✗"
        print(f"  {icon} {step.step:<15} id={step.entity_id}")
    if result.etude_url:
        print(f"\nURL de l'étude créée : {result.etude_url}")
    if not result.success:
        print(f"Erreur : {result.last_error}")
    sys.exit(0 if result.success else 1)
