"""
firebase_bridge.py — Pont Firebase pour Tomate-EJE
====================================================
Schéma Firestore RÉEL (confirmé depuis la console) :

  etudes/{id}          → nom_etude, description, profil_attendu, domaines,
                          etapes (array), etat, frais_gestion, numero,
                          client, admins, intervenants, id_intervenants,
                          id_candidatures, candidatures, documents,
                          date_creation, id, nom_interne

  etudesinternal/{id}  → Même ID que etudes. Mêmes champs +
                          id_admins, client_feedback_id,
                          intervenant_feedback_ids
                          + champs financiers (p_jeh, per_rem)

  users/{firebase_uid} → ID = Firebase UID string
                          ex: "15A6lzEMS6c1ciBx0vArrroRpi03"

  clients, templates, global, posts, feedbacks

PRÉREQUIS :
  1. serviceAccountKey.json dans le même dossier
  2. pip install firebase-admin
  3. serviceAccountKey.json dans .gitignore
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    HAS_FIREBASE = True
except ImportError:
    HAS_FIREBASE = False

COLLECTIONS = {
    "etudes":         "etudes",
    "etudesinternal": "etudesinternal",
    "users":          "users",
    "clients":        "clients",
    "templates":      "templates",
    "global":         "global",
    "posts":          "posts",
    "feedbacks":      "feedbacks",
}

DOMAINES_LABEL_TO_ID = {
    "Statistique descriptive": 1, "Analyse de données": 2,
    "Enquête/Sondage": 3, "Finance": 4, "Économie": 5,
    "Informatique": 6, "Économétrie": 7, "Séries temporelles": 8,
    "Mathématiques": 9, "Machine Learning": 10,
}

ETAT_MAP = {
    0: "brouillon", 1: "en attente", 2: "recrutement",
    3: "selection", 4: "en cours", 5: "cloturee",
}


@dataclass
class StepResult:
    ok: bool
    step: str
    entity_id: str | None = None
    entity_data: dict = field(default_factory=dict)
    error_msg: str = ""


@dataclass
class FirebasePushResult:
    success: bool
    etude_id: str | None = None
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

import json

class FirebaseBridge:
    def __init__(self, service_account_path: str = "serviceAccountKey.json"):
        if not HAS_FIREBASE:
            raise ImportError("pip install firebase-admin")
        
        sa_dict = None
        
        try:
            import streamlit as st
            if "firebase" in st.secrets:
                sa_dict = dict(st.secrets["firebase"])
        except Exception:
            pass
            
        if sa_dict is None:
            try:
                with open(service_account_path, "r") as f:
                    sa_dict = json.load(f)
            except FileNotFoundError:
                raise FileNotFoundError(f"Fichier '{service_account_path}' introuvable et pas de secrets.toml")
        
        self._sa_dict = sa_dict
        self._db = None

    def _get_db(self):
        if self._db is None:
            if not firebase_admin._apps:
                cred = credentials.Certificate(self._sa_dict)
                firebase_admin.initialize_app(cred)
            self._db = firestore.client()
        return self._db

    @staticmethod
    def _to_dt(d):
        if d is None: return None
        if isinstance(d, datetime): return d
        if isinstance(d, date): return datetime(d.year, d.month, d.day)
        return None

    def _next_numero(self, db) -> int:
        docs = list(
            db.collection(COLLECTIONS["etudes"])
            .order_by("numero", direction=firestore.Query.DESCENDING)
            .limit(1).stream()
        )
        return (docs[0].to_dict().get("numero") or 0) + 1 if docs else 1

    # ── Lecture ───────────────────────────────────────────────────────────────

    def get_etudes(self, limit: int = 50) -> list[dict]:
        db = self._get_db()
        results = []
        for doc in (db.collection(COLLECTIONS["etudes"])
                    .order_by("numero", direction=firestore.Query.DESCENDING)
                    .limit(limit).stream()):
            d = {**doc.to_dict(), "_id": doc.id}
            # Fusionner les champs internes
            internal = db.collection(COLLECTIONS["etudesinternal"]).document(doc.id).get()
            if internal.exists:
                for k in ("id_admins", "p_jeh", "per_rem",
                          "client_feedback_id", "intervenant_feedback_ids"):
                    if k in (internal.to_dict() or {}):
                        d[k] = internal.to_dict()[k]
            results.append(d)
        return results

    def get_etude(self, etude_id: str) -> dict | None:
        db = self._get_db()
        doc = db.collection(COLLECTIONS["etudes"]).document(etude_id).get()
        if not doc.exists:
            return None
        d = {**doc.to_dict(), "_id": doc.id}
        internal = db.collection(COLLECTIONS["etudesinternal"]).document(etude_id).get()
        if internal.exists:
            d.update({k: v for k, v in (internal.to_dict() or {}).items() if k not in d})
        return d

    def get_users(self) -> list[dict]:
        """Retourne tous les users. ID = Firebase UID string."""
        db = self._get_db()
        return [{**doc.to_dict(), "_id": doc.id}
                for doc in db.collection(COLLECTIONS["users"]).stream()]

    def get_clients(self) -> list[dict]:
        db = self._get_db()
        return [{**doc.to_dict(), "_id": doc.id}
                for doc in db.collection(COLLECTIONS["clients"]).stream()]

    def get_templates(self) -> list[dict]:
        db = self._get_db()
        return [{**doc.to_dict(), "_id": doc.id}
                for doc in db.collection(COLLECTIONS["templates"]).stream()]

    # ── Construction payloads ─────────────────────────────────────────────────

    def _build_etapes(self, etapes: list[dict]) -> list[dict]:
        result = []
        for i, e in enumerate(etapes):
            jeh_list = [
                {"etudiant_id": se.get("etudiant_id", ""),
                 "etudiant_nom": se.get("etudiant_nom", ""),
                 "jeh": int(se.get("jeh", 1))}
                for se in e.get("sEtapes", [])
            ]
            result.append({
                "nom":        e.get("nom", f"Étape {i+1}"),
                "details":    e.get("details", ""),
                "date_start": self._to_dt(e.get("date_start")),
                "date_end":   self._to_dt(e.get("date_end")),
                "n":          i + 1,
                "jeh":        jeh_list,
                "n_jeh":      sum(se.get("jeh", 0) for se in e.get("sEtapes", [])),
            })
        return result

    def _id_intervenants(self, session_data: dict) -> list[str]:
        return list({
            se.get("etudiant_id")
            for e in (session_data.get("etapes") or [])
            for se in e.get("sEtapes", [])
            if se.get("etudiant_id")
        })

    def _public_payload(self, sd: dict, numero: int, etude_id: str) -> dict:
        domaines = [DOMAINES_LABEL_TO_ID[l]
                    for l in (sd.get("etude_domaines") or [])
                    if l in DOMAINES_LABEL_TO_ID]
        etat = ETAT_MAP.get(int(sd.get("etude_statut_id", 0)), "brouillon")
        return {
            "id":              etude_id,
            "numero":          numero,
            "nom_etude":       sd.get("etude_nom", ""),
            "nom_interne":     sd.get("etude_nom", ""),
            "description":     sd.get("etude_but", ""),
            "profil_attendu":  sd.get("etude_competences", ""),
            "domaines":        domaines,
            "etapes":          self._build_etapes(sd.get("etapes") or []),
            "etat":            etat,
            "frais_gestion":   float(sd.get("fee", 0)),
            "client":          sd.get("client") or {},
            "admins":          [],
            "intervenants":    [],
            "id_intervenants": self._id_intervenants(sd),
            "id_candidatures": [],
            "candidatures":    [],
            "documents":       [],
            "date_creation":   firestore.SERVER_TIMESTAMP,
        }

    def _internal_payload(self, sd: dict, pub: dict, etude_id: str) -> dict:
        internal = dict(pub)
        internal.update({
            "p_jeh":                    int(sd.get("p_jeh", 400)),
            "per_rem":                  int(sd.get("per_rem", 65)),
            "id_admins":                [],
            "client_feedback_id":       None,
            "intervenant_feedback_ids": [],
        })
        return internal

    # ── Écriture ──────────────────────────────────────────────────────────────

    def push_etude(self, session_data: dict) -> StepResult:
        """
        Écrit atomiquement dans etudes/{id} ET etudesinternal/{id}.
        Même ID pour les deux — obligatoire pour que Tomate les relie.
        """
        db = self._get_db()
        try:
            numero    = self._next_numero(db)
            etude_ref = db.collection(COLLECTIONS["etudes"]).document()
            etude_id  = etude_ref.id

            pub      = self._public_payload(session_data, numero, etude_id)
            internal = self._internal_payload(session_data, pub, etude_id)

            batch = db.batch()
            batch.set(etude_ref, pub)
            batch.set(
                db.collection(COLLECTIONS["etudesinternal"]).document(etude_id),
                internal
            )
            batch.commit()

            return StepResult(ok=True, step="etude", entity_id=etude_id,
                              entity_data={"numero": numero})
        except Exception as e:
            logger.exception("push_etude failed")
            return StepResult(ok=False, step="etude", error_msg=str(e))

    def push_client(self, client: dict) -> StepResult:
        db = self._get_db()
        try:
            payload = {
                "nom":           client.get("nom", ""),
                "prenom":        client.get("prenom", ""),
                "mail":          client.get("mail", ""),
                "date_modified": firestore.SERVER_TIMESTAMP,
            }
            existing = client.get("_id")
            if existing:
                db.collection(COLLECTIONS["clients"]).document(existing).set(
                    payload, merge=True)
                return StepResult(ok=True, step="client", entity_id=existing)
            payload["date_created"] = firestore.SERVER_TIMESTAMP
            ref = db.collection(COLLECTIONS["clients"]).add(payload)[1]
            return StepResult(ok=True, step="client", entity_id=ref.id)
        except Exception as e:
            return StepResult(ok=False, step="client", error_msg=str(e))

    def update_etude(self, etude_id: str, fields: dict,
                     also_internal: bool = False) -> StepResult:
        """Met à jour des champs sur une étude existante."""
        db = self._get_db()
        try:
            fields["date_modified"] = firestore.SERVER_TIMESTAMP
            batch = db.batch()
            batch.update(
                db.collection(COLLECTIONS["etudes"]).document(etude_id), fields)
            if also_internal:
                batch.update(
                    db.collection(COLLECTIONS["etudesinternal"]).document(etude_id),
                    fields)
            batch.commit()
            return StepResult(ok=True, step="update_etude", entity_id=etude_id)
        except Exception as e:
            return StepResult(ok=False, step="update_etude", error_msg=str(e))

    # ── Pipeline complet ──────────────────────────────────────────────────────

    def push_all(self, session_data: dict) -> FirebasePushResult:
        result = FirebasePushResult(success=False)

        # Client optionnel
        client_raw = session_data.get("client") or {}
        if client_raw.get("nom"):
            step = self.push_client(client_raw)
            result.steps.append(step)
            if not step.ok:
                result.error_step, result.error_msg = "client", step.error_msg
                return result

        # Étude (double écriture atomique)
        step = self.push_etude(session_data)
        result.steps.append(step)
        if not step.ok:
            result.error_step, result.error_msg = "etude", step.error_msg
            return result

        result.success  = True
        result.etude_id = step.entity_id
        result.etude_url = (
            f"https://tomate-dev.ensaejunioretudes.fr/etude/{step.entity_id}"
        )
        return result

    # ── Connectivité ──────────────────────────────────────────────────────────

    def ping(self) -> tuple[bool, str]:
        if not HAS_FIREBASE:
            return False, "firebase-admin non installé (pip install firebase-admin)"
        try:
            db = self._get_db()
            list(db.collection(COLLECTIONS["etudes"]).limit(1).stream())
            return True, "Connexion Firestore réussie"
        except FileNotFoundError:
            return False, f"Fichier '{self._sa_path}' introuvable."
        except Exception as e:
            msg = str(e)
            if "PERMISSION_DENIED" in msg:
                return False, "Permission refusée — vérifier IAM du Service Account."
            return False, f"Erreur Firestore : {msg}"


# ── Outil d'inspection (debug) ────────────────────────────────────────────────

def inspect_schema(sa_path: str = "serviceAccountKey.json"):
    """
    Affiche les champs réels d'un document de chaque collection.
    Usage : python -c "from firebase_bridge import inspect_schema; inspect_schema()"
    """
    bridge = FirebaseBridge(sa_path)
    db = bridge._get_db()
    for name, col in COLLECTIONS.items():
        docs = list(db.collection(col).limit(1).stream())
        if docs:
            keys = sorted(docs[0].to_dict().keys())
            print(f"\n{col} ({len(keys)} champs) :")
            for k in keys:
                v = docs[0].to_dict()[k]
                print(f"  {k:<35} {type(v).__name__}")
        else:
            print(f"\n{col} : (vide)")
